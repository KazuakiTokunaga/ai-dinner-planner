targetScope = 'subscription'

@description('Azure region for all resources.')
param location string = 'japaneast'

@description('Deployment environment name.')
param environmentName string = 'dev'

@description('Resource name prefix.')
param namePrefix string = 'aidinner'

@description('Container image to deploy to Azure Container Apps.')
param containerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Foundry Hosted Agent name. Leave empty before the hosted agent is created.')
param foundryAgentName string = ''

@description('Foundry Hosted Agent instance identity principal ID. Leave empty before the hosted agent is created.')
param hostedAgentPrincipalId string = ''

@description('Microsoft Entra application client ID for Container Apps authentication. Leave empty to skip auth configuration.')
param authClientId string = ''

@description('Microsoft Entra tenant ID for Container Apps authentication.')
param authTenantId string = tenant().tenantId

@description('Container Apps secret name that stores the Microsoft Entra application client secret.')
param authClientSecretName string = 'microsoft-provider-authentication-secret'

@secure()
@description('Microsoft Entra application client secret for Container Apps authentication. Leave empty to skip Bicep auth configuration.')
param authClientSecret string = ''

@description('Whether Bicep should create RBAC assignments. Set false when equivalent assignments already exist with different names.')
param createRoleAssignments bool = true

var resourceGroupName = 'rg-${namePrefix}-${environmentName}-${location}'

resource resourceGroup 'Microsoft.Resources/resourceGroups@2024-11-01' = {
  name: resourceGroupName
  location: location
}

module app 'modules/app.bicep' = {
  name: 'app-${environmentName}'
  scope: resourceGroup
  params: {
    location: location
    environmentName: environmentName
    namePrefix: namePrefix
    containerImage: containerImage
    foundryAgentName: foundryAgentName
    hostedAgentPrincipalId: hostedAgentPrincipalId
    authClientId: authClientId
    authTenantId: authTenantId
    authClientSecretName: authClientSecretName
    authClientSecret: authClientSecret
    createRoleAssignments: createRoleAssignments
  }
}

output resourceGroup string = resourceGroup.name
output containerAppName string = app.outputs.containerAppName
output cosmosAccountName string = app.outputs.cosmosAccountName
output foundryAccountName string = app.outputs.foundryAccountName
output foundryProjectName string = app.outputs.foundryProjectName
output foundryProjectEndpoint string = app.outputs.foundryProjectEndpoint
