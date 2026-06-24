targetScope = 'resourceGroup'

param location string = resourceGroup().location
param environmentName string = 'dev'
param namePrefix string = 'aidinner'
param containerImage string
param foundryAgentName string = ''
param hostedAgentPrincipalId string = ''
param authClientId string = ''
param authTenantId string = tenant().tenantId
param authClientSecretName string = 'microsoft-provider-authentication-secret'
@secure()
param authClientSecret string = ''
param createRoleAssignments bool = true

var dashedName = '${namePrefix}-${environmentName}'
var compactName = '${namePrefix}${environmentName}'
var suffix = uniqueString(resourceGroup().id)
var cosmosAccountName = '${compactName}-cosmos-${suffix}'
var acrName = '${compactName}acr${suffix}'
var logAnalyticsName = 'log-${dashedName}-${suffix}'
var appInsightsName = 'appi-${dashedName}-${suffix}'
var containerEnvName = 'cae-${dashedName}-${suffix}'
var containerAppName = 'ca-${dashedName}-${suffix}'
var foundryAccountName = '${dashedName}-foundry-${suffix}'
var foundryProjectName = '${dashedName}-project'
var foundryProjectEndpoint = 'https://${foundryAccountName}.services.ai.azure.com/api/projects/${foundryProjectName}'
var foundryContainerEnv = empty(foundryAgentName) ? [] : [
  {
    name: 'FOUNDRY_PROJECT_ENDPOINT'
    value: foundryProjectEndpoint
  }
  {
    name: 'FOUNDRY_AGENT_NAME'
    value: foundryAgentName
  }
]
var acrPullRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '7f951dda-4ed3-4680-a7ca-43fe172d538d'
)
var containerRegistryRepositoryReaderRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'b93aa761-3e63-49ed-ac28-beffa264f7ac'
)
var cognitiveServicesUserRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'a97b65f3-24c7-4388-baec-2e87135dc908'
)
var cognitiveServicesOpenAIUserRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
)
var azureAIDeveloperRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '64702f94-c441-49e6-a78b-ef80e0188fee'
)
var cosmosDataReaderRoleDefinitionId = '${cosmosAccount.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000001'
var authEnabled = !empty(authClientId) && !empty(authClientSecret)

resource acr 'Microsoft.ContainerRegistry/registries@2025-05-01-preview' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
  }
}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2025-02-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

resource containerEnv 'Microsoft.App/managedEnvironments@2025-01-01' = {
  name: containerEnvName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2025-05-01-preview' = {
  name: cosmosAccountName
  location: location
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    disableLocalAuth: true
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2025-05-01-preview' = {
  parent: cosmosAccount
  name: 'dinner-planner'
  properties: {
    resource: {
      id: 'dinner-planner'
    }
  }
}

resource menusContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2025-05-01-preview' = {
  parent: database
  name: 'menus'
  properties: {
    resource: {
      id: 'menus'
      partitionKey: {
        paths: [
          '/category'
          '/main_ingredient'
          '/id'
        ]
        kind: 'MultiHash'
        version: 2
      }
    }
  }
}

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: foundryAccountName
  location: location
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    customSubDomainName: foundryAccountName
    disableLocalAuth: true
    allowProjectManagement: true
    publicNetworkAccess: 'Enabled'
  }
}

resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: foundryAccount
  name: foundryProjectName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {}
}

resource containerApp 'Microsoft.App/containerApps@2025-01-01' = {
  name: containerAppName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: containerEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
      }
      secrets: authEnabled ? [
        {
          name: authClientSecretName
          value: authClientSecret
        }
      ] : []
    }
    template: {
      containers: [
        {
          name: 'web'
          image: containerImage
          env: concat([
            {
              name: 'APP_ENV'
              value: environmentName
            }
            {
              name: 'USE_COSMOS'
              value: 'true'
            }
            {
              name: 'COSMOS_ENDPOINT'
              value: cosmosAccount.properties.documentEndpoint
            }
            {
              name: 'COSMOS_DATABASE_NAME'
              value: database.name
            }
            {
              name: 'COSMOS_CONTAINER_NAME'
              value: menusContainer.name
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsights.properties.ConnectionString
            }
          ], foundryContainerEnv)
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
      }
    }
  }
}

resource containerAppAuth 'Microsoft.App/containerApps/authConfigs@2025-07-01' = if (authEnabled) {
  parent: containerApp
  name: 'current'
  properties: {
    platform: {
      enabled: true
    }
    globalValidation: {
      unauthenticatedClientAction: 'RedirectToLoginPage'
      redirectToProvider: 'azureActiveDirectory'
    }
    identityProviders: {
      azureActiveDirectory: {
        isAutoProvisioned: false
        registration: {
          clientId: authClientId
          clientSecretSettingName: authClientSecretName
          openIdIssuer: '${environment().authentication.loginEndpoint}${authTenantId}/v2.0'
        }
      }
    }
    login: {
      preserveUrlFragmentsForLogins: false
    }
  }
}

resource containerAppAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (createRoleAssignments) {
  name: guid(acr.id, containerApp.id, 'AcrPull')
  scope: acr
  properties: {
    roleDefinitionId: acrPullRoleDefinitionId
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource containerAppCosmosReader 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2025-05-01-preview' = if (createRoleAssignments) {
  parent: cosmosAccount
  name: guid(cosmosAccount.id, containerApp.id, 'Cosmos DB Built-in Data Reader')
  properties: {
    principalId: containerApp.identity.principalId
    roleDefinitionId: cosmosDataReaderRoleDefinitionId
    scope: cosmosAccount.id
  }
}

resource containerAppFoundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (createRoleAssignments) {
  name: guid(foundryAccount.id, containerApp.id, 'Cognitive Services User')
  scope: foundryAccount
  properties: {
    roleDefinitionId: cognitiveServicesUserRoleDefinitionId
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource containerAppFoundryOpenAIUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (createRoleAssignments) {
  name: guid(foundryAccount.id, containerApp.id, 'Cognitive Services OpenAI User')
  scope: foundryAccount
  properties: {
    roleDefinitionId: cognitiveServicesOpenAIUserRoleDefinitionId
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource containerAppFoundryProjectDeveloper 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (createRoleAssignments) {
  name: guid(foundryProject.id, containerApp.id, 'Azure AI Developer')
  scope: foundryProject
  properties: {
    roleDefinitionId: azureAIDeveloperRoleDefinitionId
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource foundryProjectAcrRepositoryReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (createRoleAssignments) {
  name: guid(acr.id, foundryProject.id, 'Container Registry Repository Reader')
  scope: acr
  properties: {
    roleDefinitionId: containerRegistryRepositoryReaderRoleDefinitionId
    principalId: foundryProject.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource hostedAgentCosmosReader 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2025-05-01-preview' = if (createRoleAssignments && !empty(hostedAgentPrincipalId)) {
  parent: cosmosAccount
  name: guid(cosmosAccount.id, hostedAgentPrincipalId, 'Cosmos DB Built-in Data Reader')
  properties: {
    principalId: hostedAgentPrincipalId
    roleDefinitionId: cosmosDataReaderRoleDefinitionId
    scope: cosmosAccount.id
  }
}

output containerAppName string = containerApp.name
output cosmosAccountName string = cosmosAccount.name
output foundryAccountName string = foundryAccount.name
output foundryProjectName string = foundryProject.name
output foundryProjectEndpoint string = foundryProjectEndpoint
