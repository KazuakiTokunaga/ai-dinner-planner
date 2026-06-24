import argparse
import json
import os
import subprocess
from typing import Any

DEFAULT_AUTH_SECRET_NAME = "microsoft-provider-authentication-secret"


def env_value(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value else default


def required_value(value: str | None, name: str) -> str:
    if not value:
        raise ValueError(f"{name} is required")
    return value


def run_az(arguments: list[str], *, allow_failure: bool = False) -> str:
    result = subprocess.run(
        ["az", *arguments],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0 and not allow_failure:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def run_az_json(arguments: list[str], *, allow_failure: bool = False) -> Any:
    output = run_az([*arguments, "-o", "json"], allow_failure=allow_failure)
    if not output:
        return None
    return json.loads(output)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Configure Microsoft Entra authentication for an Azure Container App."
    )
    parser.add_argument("--subscription-id", default=env_value("AZURE_SUBSCRIPTION_ID"))
    parser.add_argument("--tenant-id", default=env_value("AZURE_TENANT_ID"))
    parser.add_argument("--resource-group", default=env_value("RESOURCE_GROUP"))
    parser.add_argument("--container-app-name", default=env_value("CONTAINER_APP_NAME"))
    parser.add_argument("--deployment-name", default=env_value("DEPLOYMENT_NAME"))
    parser.add_argument("--location", default=env_value("LOCATION"))
    parser.add_argument("--environment-name", default=env_value("ENVIRONMENT_NAME"))
    parser.add_argument("--name-prefix", default=env_value("NAME_PREFIX"))
    parser.add_argument("--container-image", default=env_value("WEB_IMAGE"))
    parser.add_argument("--foundry-agent-name", default=env_value("FOUNDRY_AGENT_NAME"))
    parser.add_argument(
        "--hosted-agent-principal-id",
        default=env_value("HOSTED_AGENT_PRINCIPAL_ID"),
    )
    parser.add_argument(
        "--auth-app-name", default=env_value("AUTH_APP_NAME", "ai-dinner-planner-auth")
    )
    parser.add_argument("--auth-app-id", default=env_value("AUTH_APP_ID"))
    parser.add_argument(
        "--auth-client-secret-name",
        default=env_value("AUTH_CLIENT_SECRET_NAME", DEFAULT_AUTH_SECRET_NAME),
    )
    parser.add_argument("--target-user-email", default=env_value("AUTH_TARGET_USER_EMAIL"))
    parser.add_argument("--target-user-id", default=env_value("AUTH_TARGET_USER_ID"))
    parser.add_argument(
        "--invite-redirect-url",
        default=env_value("AUTH_INVITE_REDIRECT_URL", "https://myapps.microsoft.com"),
    )
    parser.add_argument(
        "--skip-invite",
        action="store_true",
        help="Do not invite the target user if the user cannot be found.",
    )
    parser.add_argument(
        "--skip-restart",
        action="store_true",
        help="Do not restart the active revision after updating the auth secret.",
    )
    parser.add_argument(
        "--deploy-bicep",
        action="store_true",
        help="Deploy infra/main.bicep with the generated auth client secret.",
    )
    parser.add_argument(
        "--template-file",
        default=env_value("BICEP_TEMPLATE_FILE", "infra/main.bicep"),
    )
    parser.add_argument(
        "--create-role-assignments",
        action=argparse.BooleanOptionalAction,
        default=env_value("CREATE_ROLE_ASSIGNMENTS", "true").lower() == "true",
        help="Whether the Bicep deployment should create RBAC assignments.",
    )
    return parser


def get_container_app_url(resource_group: str, container_app_name: str) -> str:
    fqdn = run_az(
        [
            "containerapp",
            "show",
            "--resource-group",
            resource_group,
            "--name",
            container_app_name,
            "--query",
            "properties.configuration.ingress.fqdn",
            "-o",
            "tsv",
        ]
    )
    return f"https://{fqdn}"


def ensure_auth_application(auth_app_name: str, auth_app_id: str | None, redirect_uri: str) -> str:
    if auth_app_id:
        app_id = auth_app_id
    else:
        applications = run_az_json(
            ["ad", "app", "list", "--display-name", auth_app_name],
        )
        app_id = applications[0]["appId"] if applications else ""

    if app_id:
        run_az(
            [
                "ad",
                "app",
                "update",
                "--id",
                app_id,
                "--web-redirect-uris",
                redirect_uri,
                "--enable-id-token-issuance",
                "true",
            ]
        )
        return app_id

    return run_az(
        [
            "ad",
            "app",
            "create",
            "--display-name",
            auth_app_name,
            "--sign-in-audience",
            "AzureADMyOrg",
            "--web-redirect-uris",
            redirect_uri,
            "--enable-id-token-issuance",
            "true",
            "--query",
            "appId",
            "-o",
            "tsv",
        ]
    )


def ensure_service_principal(auth_app_id: str) -> str:
    run_az(["ad", "sp", "create", "--id", auth_app_id], allow_failure=True)
    return run_az(["ad", "sp", "show", "--id", auth_app_id, "--query", "id", "-o", "tsv"])


def find_user_id_by_email(email: str) -> str | None:
    user = run_az_json(
        ["ad", "user", "show", "--id", email],
        allow_failure=True,
    )
    if user and user.get("id"):
        return user["id"]

    users = run_az_json(
        ["ad", "user", "list", "--filter", f"mail eq '{email}'"],
        allow_failure=True,
    )
    if users:
        return users[0]["id"]

    return None


def invite_user(email: str, invite_redirect_url: str) -> str:
    invitation = run_az_json(
        [
            "rest",
            "--method",
            "POST",
            "--uri",
            "https://graph.microsoft.com/v1.0/invitations",
            "--headers",
            "Content-Type=application/json",
            "--body",
            json.dumps(
                {
                    "invitedUserEmailAddress": email,
                    "inviteRedirectUrl": invite_redirect_url,
                    "sendInvitationMessage": True,
                }
            ),
        ]
    )
    return invitation["invitedUser"]["id"]


def resolve_target_user_id(
    target_user_id: str | None,
    target_user_email: str | None,
    invite_redirect_url: str,
    skip_invite: bool,
) -> str:
    if target_user_id:
        return target_user_id
    email = required_value(target_user_email, "--target-user-email or AUTH_TARGET_USER_EMAIL")
    found_user_id = find_user_id_by_email(email)
    if found_user_id:
        return found_user_id
    if skip_invite:
        raise ValueError(f"Target user was not found: {email}")
    return invite_user(email, invite_redirect_url)


def ensure_user_assignment(target_user_id: str, auth_app_id: str, auth_sp_object_id: str) -> None:
    run_az(
        [
            "ad",
            "sp",
            "update",
            "--id",
            auth_app_id,
            "--set",
            "appRoleAssignmentRequired=true",
        ]
    )
    assignments = run_az_json(
        [
            "rest",
            "--method",
            "GET",
            "--uri",
            f"https://graph.microsoft.com/v1.0/users/{target_user_id}/appRoleAssignments",
        ]
    )
    for assignment in assignments.get("value", []):
        if assignment.get("resourceId") == auth_sp_object_id:
            return

    run_az_json(
        [
            "rest",
            "--method",
            "POST",
            "--uri",
            f"https://graph.microsoft.com/v1.0/users/{target_user_id}/appRoleAssignments",
            "--headers",
            "Content-Type=application/json",
            "--body",
            json.dumps(
                {
                    "principalId": target_user_id,
                    "resourceId": auth_sp_object_id,
                    "appRoleId": "00000000-0000-0000-0000-000000000000",
                }
            ),
        ]
    )


def configure_container_app_auth(
    resource_group: str,
    container_app_name: str,
    tenant_id: str,
    auth_app_id: str,
    auth_client_secret_name: str,
) -> str:
    client_secret = run_az(
        [
            "ad",
            "app",
            "credential",
            "reset",
            "--id",
            auth_app_id,
            "--display-name",
            "container-app-auth",
            "--query",
            "password",
            "-o",
            "tsv",
        ]
    )
    microsoft_auth_arguments = [
        "containerapp",
        "auth",
        "microsoft",
        "update",
        "--resource-group",
        resource_group,
        "--name",
        container_app_name,
        "--client-id",
        auth_app_id,
        "--tenant-id",
        tenant_id,
        "--yes",
    ]
    if auth_client_secret_name == DEFAULT_AUTH_SECRET_NAME:
        microsoft_auth_arguments.extend(["--client-secret", client_secret])
    else:
        run_az(
            [
                "containerapp",
                "secret",
                "set",
                "--resource-group",
                resource_group,
                "--name",
                container_app_name,
                "--secrets",
                f"{auth_client_secret_name}={client_secret}",
            ]
        )
        microsoft_auth_arguments.extend(["--client-secret-name", auth_client_secret_name])

    run_az(microsoft_auth_arguments)
    run_az(
        [
            "containerapp",
            "auth",
            "update",
            "--resource-group",
            resource_group,
            "--name",
            container_app_name,
            "--enabled",
            "true",
            "--unauthenticated-client-action",
            "RedirectToLoginPage",
            "--redirect-provider",
            "azureActiveDirectory",
        ]
    )
    return client_secret


def deploy_bicep(
    args: argparse.Namespace,
    tenant_id: str,
    auth_app_id: str,
    auth_client_secret: str,
) -> None:
    deployment_name = required_value(args.deployment_name, "--deployment-name or DEPLOYMENT_NAME")
    location = required_value(args.location, "--location or LOCATION")
    environment_name = required_value(
        args.environment_name,
        "--environment-name or ENVIRONMENT_NAME",
    )
    name_prefix = required_value(args.name_prefix, "--name-prefix or NAME_PREFIX")
    container_image = required_value(args.container_image, "--container-image or WEB_IMAGE")
    foundry_agent_name = required_value(
        args.foundry_agent_name,
        "--foundry-agent-name or FOUNDRY_AGENT_NAME",
    )
    hosted_agent_principal_id = required_value(
        args.hosted_agent_principal_id,
        "--hosted-agent-principal-id or HOSTED_AGENT_PRINCIPAL_ID",
    )

    run_az(
        [
            "deployment",
            "sub",
            "create",
            "--name",
            deployment_name,
            "--location",
            location,
            "--template-file",
            args.template_file,
            "--parameters",
            f"location={location}",
            f"environmentName={environment_name}",
            f"namePrefix={name_prefix}",
            f"containerImage={container_image}",
            f"foundryAgentName={foundry_agent_name}",
            f"hostedAgentPrincipalId={hosted_agent_principal_id}",
            f"authClientId={auth_app_id}",
            f"authTenantId={tenant_id}",
            f"authClientSecretName={args.auth_client_secret_name}",
            f"authClientSecret={auth_client_secret}",
            f"createRoleAssignments={str(args.create_role_assignments).lower()}",
        ]
    )


def restart_active_revision(resource_group: str, container_app_name: str) -> None:
    active_revision = run_az(
        [
            "containerapp",
            "revision",
            "list",
            "--resource-group",
            resource_group,
            "--name",
            container_app_name,
            "--query",
            "[?properties.active].name | [0]",
            "-o",
            "tsv",
        ]
    )
    if not active_revision:
        return
    run_az(
        [
            "containerapp",
            "revision",
            "restart",
            "--resource-group",
            resource_group,
            "--name",
            container_app_name,
            "--revision",
            active_revision,
        ]
    )


def main() -> None:
    args = build_parser().parse_args()
    subscription_id = required_value(
        args.subscription_id, "--subscription-id or AZURE_SUBSCRIPTION_ID"
    )
    tenant_id = required_value(args.tenant_id, "--tenant-id or AZURE_TENANT_ID")
    resource_group = required_value(args.resource_group, "--resource-group or RESOURCE_GROUP")
    container_app_name = required_value(
        args.container_app_name,
        "--container-app-name or CONTAINER_APP_NAME",
    )
    auth_app_name = required_value(args.auth_app_name, "--auth-app-name or AUTH_APP_NAME")

    run_az(["account", "set", "--subscription", subscription_id])

    app_url = get_container_app_url(resource_group, container_app_name)
    redirect_uri = f"{app_url}/.auth/login/aad/callback"
    auth_app_id = ensure_auth_application(args.auth_app_name, args.auth_app_id, redirect_uri)
    auth_sp_object_id = ensure_service_principal(auth_app_id)
    target_user_id = resolve_target_user_id(
        args.target_user_id,
        args.target_user_email,
        args.invite_redirect_url,
        args.skip_invite,
    )
    ensure_user_assignment(target_user_id, auth_app_id, auth_sp_object_id)
    auth_client_secret = configure_container_app_auth(
        resource_group,
        container_app_name,
        tenant_id,
        auth_app_id,
        args.auth_client_secret_name,
    )
    if args.deploy_bicep:
        deploy_bicep(args, tenant_id, auth_app_id, auth_client_secret)
    if not args.skip_restart:
        restart_active_revision(resource_group, container_app_name)

    print("Container App authentication configured.")
    print(f"AUTH_APP_NAME={auth_app_name}")
    print(f"AUTH_APP_ID={auth_app_id}")
    print(f"AUTH_SP_OBJECT_ID={auth_sp_object_id}")
    print(f"AUTH_TARGET_USER_ID={target_user_id}")
    print(f"AUTH_CLIENT_SECRET_NAME={args.auth_client_secret_name}")
    print("Bicep parameters:")
    print(f"  authClientId={auth_app_id}")
    print(f"  authTenantId={tenant_id}")
    print(f"  authClientSecretName={args.auth_client_secret_name}")
    if args.deploy_bicep:
        print("Bicep deployment synchronized authConfig.")


if __name__ == "__main__":
    main()
