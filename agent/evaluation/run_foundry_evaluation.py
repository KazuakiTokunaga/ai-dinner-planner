import argparse
import os
import time
from pathlib import Path
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    EvaluatorCategory,
    EvaluatorMetric,
    EvaluatorMetricDirection,
    EvaluatorMetricType,
    EvaluatorType,
    EvaluatorVersion,
    PromptBasedEvaluatorDefinition,
)
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from openai.types.evals.create_eval_jsonl_run_data_source_param import (
    CreateEvalJSONLRunDataSourceParam,
    SourceFileContent,
    SourceFileContentContent,
)

EVALUATION_DIR = Path(__file__).parent
DEFAULT_SINGLE_TURN_DATASET_PATH = EVALUATION_DIR / "dataset.jsonl"
DEFAULT_SIMULATION_DATASET_PATH = EVALUATION_DIR / "simulation_scenarios.jsonl"

SINGLE_TURN_DATA_SOURCE_CONFIG = {
    "type": "custom",
    "item_schema": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "query": {"type": "string"},
            "ground_truth": {"type": "string"},
        },
        "required": ["query", "ground_truth"],
    },
    "include_sample_schema": True,
}

CONVERSATION_DATA_SOURCE_CONFIG = {
    "type": "custom",
    "item_schema": {
        "type": "object",
        "properties": {
            "messages": {"type": "array"},
            "test_case_description": {"type": "string"},
        },
        "required": ["messages"],
    },
    "include_sample_schema": False,
}

CUSTOM_CONVERSATION_DATA_SOURCE_CONFIG = {
    "type": "custom",
    "item_schema": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "test_case_id": {"type": "string"},
            "messages": {"type": "array"},
        },
        "required": ["messages"],
    },
    "include_sample_schema": False,
}

TURN_JUDGE_PROMPT = """
You are an evaluator for a dinner-planning AI agent.

Compare the assistant response with the ground truth for the given user query.
Return true only when the response satisfies the expected behavior and constraints.
Return false when the response contradicts the ground truth, omits the main requirement,
uses web search when it should not, invents unsupported facts, or fails to answer the task.

Query:
{{query}}

Assistant response:
{{response}}

Ground truth:
{{ground_truth}}

Return only valid JSON in this format:
{"result": true, "reason": "brief explanation"}
""".strip()

CONVERSATION_JUDGE_PROMPT = """
You are an evaluator for a dinner-planning AI agent.

Evaluate the full conversation transcript for product-specific quality. Return true only when
the agent satisfies the user's dinner-planning goal, prioritizes registered menu information,
does not invent unsupported registered-menu facts, avoids web search unless explicitly asked,
and gives ingredient or shopping-list details at a practical level when requested.

Test case id:
{{test_case_id}}

Conversation messages:
{{messages}}

Return only valid JSON in this format:
{"result": true, "reason": "brief explanation"}
""".strip()


def env_value(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value else default


def required_value(value: str | None, name: str) -> str:
    if not value:
        raise ValueError(f"{name} is required")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run single-turn and simulation evaluations for the hosted agent."
    )
    parser.add_argument(
        "--suite",
        choices=["single-turn", "simulation", "all"],
        default="all",
        help="Evaluation suite to run.",
    )
    parser.add_argument("--project-endpoint", default=env_value("FOUNDRY_PROJECT_ENDPOINT"))
    parser.add_argument("--agent-name", default=env_value("FOUNDRY_AGENT_NAME"))
    parser.add_argument("--agent-version", default=env_value("FOUNDRY_AGENT_VERSION"))
    parser.add_argument(
        "--judge-model",
        default=env_value("FOUNDRY_EVALUATION_MODEL", env_value("AZURE_AI_MODEL_DEPLOYMENT_NAME")),
        help="Model deployment used by the LLM judge.",
    )
    parser.add_argument(
        "--single-turn-dataset-file",
        type=Path,
        default=DEFAULT_SINGLE_TURN_DATASET_PATH,
    )
    parser.add_argument(
        "--simulation-dataset-file",
        type=Path,
        default=DEFAULT_SIMULATION_DATASET_PATH,
    )
    parser.add_argument(
        "--dataset-version",
        default=env_value("FOUNDRY_EVAL_DATASET_VERSION", "1"),
        help="Dataset version used for all evaluation suites.",
    )
    parser.add_argument(
        "--single-turn-dataset-name",
        default=env_value("FOUNDRY_EVAL_SINGLE_TURN_DATASET_NAME", "ai-dinner-planner-eval"),
    )
    parser.add_argument(
        "--simulation-dataset-name",
        default=env_value(
            "FOUNDRY_EVAL_SIMULATION_DATASET_NAME",
            "ai-dinner-planner-simulation-eval",
        ),
    )
    parser.add_argument(
        "--name-prefix",
        default=env_value("FOUNDRY_EVAL_NAME_PREFIX", "ai-dinner-planner"),
        help="Prefix used for evaluation and run names.",
    )
    parser.add_argument(
        "--evaluator-name",
        default=env_value(
            "FOUNDRY_EVAL_EVALUATOR_NAME",
            "ai_dinner_planner_gt_binary_judge",
        ),
    )
    parser.add_argument(
        "--evaluator-version",
        default=env_value("FOUNDRY_EVAL_EVALUATOR_VERSION"),
        help="Turn evaluator version to use. If omitted, the latest existing version is reused.",
    )
    parser.add_argument(
        "--conversation-evaluator-name",
        default=env_value(
            "FOUNDRY_EVAL_CONVERSATION_EVALUATOR_NAME",
            "ai_dinner_planner_conversation_binary_judge",
        ),
    )
    parser.add_argument(
        "--conversation-evaluator-version",
        default=env_value("FOUNDRY_EVAL_CONVERSATION_EVALUATOR_VERSION"),
        help="Custom conversation evaluator version to use for generated simulation logs.",
    )
    parser.add_argument(
        "--simulation-num-conversations",
        type=int,
        default=int(env_value("FOUNDRY_EVAL_SIMULATION_NUM_CONVERSATIONS", "1")),
    )
    parser.add_argument(
        "--simulation-max-turns",
        type=int,
        default=int(env_value("FOUNDRY_EVAL_SIMULATION_MAX_TURNS", "5")),
    )
    parser.add_argument(
        "--protocol",
        choices=["responses", "invocations"],
        default=env_value("FOUNDRY_AGENT_PROTOCOL", "responses"),
        help="Use responses for ResponsesHostServer agents, or invocations protocol.",
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Poll until the evaluation run finishes.",
    )
    parser.add_argument("--poll-seconds", type=int, default=5)
    return parser


def create_turn_evaluator_version() -> EvaluatorVersion:
    return EvaluatorVersion(
        evaluator_type=EvaluatorType.CUSTOM,
        categories=[EvaluatorCategory.QUALITY],
        display_name="AI Dinner Planner ground-truth binary judge",
        description="Binary LLM judge comparing agent output with a ground-truth expectation.",
        definition=PromptBasedEvaluatorDefinition(
            prompt_text=TURN_JUDGE_PROMPT,
            init_parameters={
                "type": "object",
                "properties": {
                    "deployment_name": {"type": "string"},
                    "threshold": {"type": "number"},
                },
                "required": ["deployment_name", "threshold"],
            },
            data_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "response": {"type": "string"},
                    "ground_truth": {"type": "string"},
                },
                "required": ["query", "response", "ground_truth"],
            },
            metrics={
                "ground_truth_passed": EvaluatorMetric(
                    type=EvaluatorMetricType.BOOLEAN,
                    desirable_direction=EvaluatorMetricDirection.INCREASE,
                    threshold=1,
                    is_primary=True,
                )
            },
        ),
    )


def create_conversation_evaluator_version() -> EvaluatorVersion:
    return EvaluatorVersion(
        evaluator_type=EvaluatorType.CUSTOM,
        categories=[EvaluatorCategory.QUALITY],
        display_name="AI Dinner Planner conversation binary judge",
        description="Binary LLM judge for generated dinner-planning conversation logs.",
        definition=PromptBasedEvaluatorDefinition(
            prompt_text=CONVERSATION_JUDGE_PROMPT,
            init_parameters={
                "type": "object",
                "properties": {
                    "deployment_name": {"type": "string"},
                    "threshold": {"type": "number"},
                },
                "required": ["deployment_name", "threshold"],
            },
            data_schema={
                "type": "object",
                "properties": {
                    "messages": {"type": "array"},
                    "test_case_id": {"type": "string"},
                },
                "required": ["messages"],
            },
            metrics={
                "conversation_passed": EvaluatorMetric(
                    type=EvaluatorMetricType.BOOLEAN,
                    desirable_direction=EvaluatorMetricDirection.INCREASE,
                    threshold=1,
                    is_primary=True,
                )
            },
        ),
    )


def custom_evaluator_criterion(
    evaluator_name: str,
    judge_model: str,
    data_mapping: dict[str, str],
    evaluator_version: str | None,
) -> dict[str, Any]:
    criterion = {
        "type": "azure_ai_evaluator",
        "name": evaluator_name,
        "evaluator_name": evaluator_name,
        "initialization_parameters": {
            "deployment_name": judge_model,
            "threshold": 1,
        },
        "data_mapping": data_mapping,
    }
    if evaluator_version:
        criterion["evaluator_version"] = evaluator_version
    return criterion


def conversation_builtin_criteria(judge_model: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "azure_ai_evaluator",
            "name": name,
            "evaluator_name": evaluator_name,
            "initialization_parameters": {"model": judge_model},
            "data_mapping": {"messages": "{{item.messages}}"},
        }
        for name, evaluator_name in [
            ("task_completion", "builtin.task_completion"),
            ("conversation_coherence", "builtin.coherence"),
            ("customer_satisfaction", "builtin.customer_satisfaction"),
        ]
    ]


def upload_or_get_dataset(
    project_client: AIProjectClient,
    name: str,
    version: str,
    dataset_file: Path,
) -> str:
    try:
        dataset = project_client.datasets.upload_file(
            name=name,
            version=version,
            file_path=str(dataset_file),
        )
    except ResourceExistsError:
        dataset = project_client.datasets.get(name=name, version=version)
    return dataset.id


def get_latest_evaluator_version(project_client: AIProjectClient, name: str) -> str | None:
    try:
        versions = list(project_client.beta.evaluators.list_versions(name=name, type="custom"))
    except ResourceNotFoundError:
        return None
    if not versions:
        return None
    return max(versions, key=lambda evaluator: int(evaluator.version)).version


def resolve_custom_evaluator_version(
    project_client: AIProjectClient,
    name: str,
    pinned_version: str | None,
    evaluator_version: EvaluatorVersion,
    label: str,
) -> str:
    if pinned_version:
        evaluator = project_client.beta.evaluators.get_version(name=name, version=pinned_version)
    else:
        latest_version = get_latest_evaluator_version(project_client, name)
        evaluator = (
            project_client.beta.evaluators.get_version(name=name, version=latest_version)
            if latest_version
            else project_client.beta.evaluators.create_version(
                name=name,
                evaluator_version=evaluator_version,
            )
        )
    print(f"{label} evaluator id: {evaluator.id}")
    print(f"{label} evaluator version: {evaluator.version}")
    return evaluator.version


def agent_target(args: argparse.Namespace) -> dict[str, Any]:
    target = {
        "type": "azure_ai_agent",
        "name": args.agent_name,
    }
    if args.agent_version:
        target["version"] = args.agent_version
    return target


def run_eval(
    openai_client: Any,
    suite: str,
    evaluation_name: str,
    run_name: str,
    data_source_config: dict[str, Any],
    testing_criteria: list[dict[str, Any]],
    data_source: dict[str, Any] | CreateEvalJSONLRunDataSourceParam,
    args: argparse.Namespace,
    extra_body: dict[str, Any] | None = None,
) -> list[Any]:
    eval_object = openai_client.evals.create(
        name=evaluation_name,
        data_source_config=data_source_config,
        testing_criteria=testing_criteria,
    )
    print(f"[{suite}] evaluation id: {eval_object.id}")

    eval_run_kwargs = {
        "eval_id": eval_object.id,
        "name": run_name,
        "data_source": data_source,
    }
    if extra_body:
        eval_run_kwargs["extra_body"] = extra_body
    eval_run = openai_client.evals.runs.create(**eval_run_kwargs)
    print(f"[{suite}] evaluation run id: {eval_run.id}")

    if not args.wait:
        return []
    return print_run_result(
        openai_client,
        eval_object.id,
        eval_run.id,
        args.poll_seconds,
        suite,
    )


def print_run_result(
    openai_client: Any,
    eval_id: str,
    run_id: str,
    poll_seconds: int,
    suite: str,
) -> list[Any]:
    while True:
        run = openai_client.evals.runs.retrieve(run_id=run_id, eval_id=eval_id)
        print(f"[{suite}] evaluation run status: {run.status}")
        if run.status in {"completed", "failed", "cancelled"}:
            break
        time.sleep(poll_seconds)

    output_items = list(openai_client.evals.runs.output_items.list(run_id=run.id, eval_id=eval_id))
    print(f"[{suite}] evaluation run id: {run.id}")
    print(f"report url: {getattr(run, 'report_url', None)}")
    for item in output_items:
        datasource_item = getattr(item, "datasource_item", {}) or {}
        item_id = datasource_item.get("id", getattr(item, "id", "unknown"))
        query = datasource_item.get("query") or datasource_item.get("test_case_description") or ""
        for result in getattr(item, "results", []) or []:
            print(
                " | ".join(
                    [
                        f"item={item_id}",
                        f"suite={suite}",
                        f"label={getattr(result, 'label', None)}",
                        f"passed={getattr(result, 'passed', None)}",
                        f"reason={getattr(result, 'reason', '')}",
                    ]
                )
            )
        print(f"query={query}")
    return output_items


def run_single_turn(
    project_client: AIProjectClient,
    openai_client: Any,
    args: argparse.Namespace,
) -> None:
    print("[single-turn] starting")
    evaluator_version = resolve_custom_evaluator_version(
        project_client,
        args.evaluator_name,
        args.evaluator_version,
        create_turn_evaluator_version(),
        "turn",
    )
    dataset_id = upload_or_get_dataset(
        project_client=project_client,
        name=args.single_turn_dataset_name,
        version=args.dataset_version,
        dataset_file=args.single_turn_dataset_file.resolve(),
    )
    print(f"[single-turn] dataset id: {dataset_id}")

    input_messages = (
        {"message": "{{item.query}}"}
        if args.protocol == "invocations"
        else {
            "type": "template",
            "template": [
                {
                    "type": "message",
                    "role": "user",
                    "content": {
                        "type": "input_text",
                        "text": "{{item.query}}",
                    },
                }
            ],
        }
    )
    run_eval(
        openai_client=openai_client,
        suite="single-turn",
        evaluation_name=f"{args.name_prefix}-single-turn-eval",
        run_name=f"{args.name_prefix}-single-turn-run",
        data_source_config=SINGLE_TURN_DATA_SOURCE_CONFIG,
        testing_criteria=[
            custom_evaluator_criterion(
                args.evaluator_name,
                args.judge_model,
                {
                    "query": "{{item.query}}",
                    "response": "{{sample.output_text}}",
                    "ground_truth": "{{item.ground_truth}}",
                },
                evaluator_version,
            )
        ],
        data_source={
            "type": "azure_ai_target_completions",
            "source": {"type": "file_id", "id": dataset_id},
            "input_messages": input_messages,
            "target": agent_target(args),
        },
        args=args,
    )


def run_simulation(
    project_client: AIProjectClient,
    openai_client: Any,
    args: argparse.Namespace,
) -> None:
    print("[simulation] starting")
    dataset_id = upload_or_get_dataset(
        project_client=project_client,
        name=args.simulation_dataset_name,
        version=args.dataset_version,
        dataset_file=args.simulation_dataset_file.resolve(),
    )
    print(f"[simulation] dataset id: {dataset_id}")

    output_items = run_eval(
        openai_client=openai_client,
        suite="simulation",
        evaluation_name=f"{args.name_prefix}-simulation-eval",
        run_name=f"{args.name_prefix}-simulation-run",
        data_source_config=CONVERSATION_DATA_SOURCE_CONFIG,
        testing_criteria=conversation_builtin_criteria(args.judge_model),
        data_source={
            "type": "azure_ai_target_completions",
            "source": {"type": "file_id", "id": dataset_id},
            "target": agent_target(args),
            "item_generation_params": {
                "type": "conversation_gen_preview",
                "model": args.judge_model,
                "num_conversations": args.simulation_num_conversations,
                "max_turns": args.simulation_max_turns,
                "sampling_params": {
                    "temperature": 0.7,
                    "top_p": 1.0,
                    "max_completion_tokens": 800,
                },
                "data_mapping": {
                    "test_case_description": "test_case_description",
                    "id": "id",
                    "desired_num_turns": "desired_num_turns",
                },
            },
        },
        args=args,
        extra_body={"evaluation_level": "conversation"},
    )
    if args.wait:
        run_custom_conversation_judge(project_client, openai_client, args, output_items)


def run_custom_conversation_judge(
    project_client: AIProjectClient,
    openai_client: Any,
    args: argparse.Namespace,
    output_items: list[Any],
) -> None:
    generated_content = []
    for item in output_items:
        datasource_item = getattr(item, "datasource_item", {}) or {}
        messages = datasource_item.get("messages")
        if messages:
            generated_content.append(
                SourceFileContentContent(
                    item={
                        "id": datasource_item.get("id", getattr(item, "id", "unknown")),
                        "test_case_id": datasource_item.get("test_case_id", ""),
                        "messages": messages,
                    }
                )
            )
    if not generated_content:
        print("[simulation-custom] skipped: no generated conversation messages found")
        return

    evaluator_version = resolve_custom_evaluator_version(
        project_client,
        args.conversation_evaluator_name,
        args.conversation_evaluator_version,
        create_conversation_evaluator_version(),
        "conversation",
    )
    run_eval(
        openai_client=openai_client,
        suite="simulation-custom",
        evaluation_name=f"{args.name_prefix}-simulation-custom-eval",
        run_name=f"{args.name_prefix}-simulation-custom-run",
        data_source_config=CUSTOM_CONVERSATION_DATA_SOURCE_CONFIG,
        testing_criteria=[
            custom_evaluator_criterion(
                args.conversation_evaluator_name,
                args.judge_model,
                {
                    "messages": "{{item.messages}}",
                    "test_case_id": "{{item.test_case_id}}",
                },
                evaluator_version,
            )
        ],
        data_source=CreateEvalJSONLRunDataSourceParam(
            type="jsonl",
            source=SourceFileContent(
                type="file_content",
                content=generated_content,
            ),
        ),
        args=args,
    )


def validate_dataset_files(args: argparse.Namespace) -> None:
    files = []
    if args.suite in {"single-turn", "all"}:
        files.append(args.single_turn_dataset_file)
    if args.suite in {"simulation", "all"}:
        files.append(args.simulation_dataset_file)
    for dataset_file in files:
        if not dataset_file.resolve().exists():
            raise FileNotFoundError(dataset_file)


def main() -> None:
    args = build_parser().parse_args()
    args.project_endpoint = required_value(
        args.project_endpoint,
        "--project-endpoint or FOUNDRY_PROJECT_ENDPOINT",
    )
    args.agent_name = required_value(args.agent_name, "--agent-name or FOUNDRY_AGENT_NAME")
    args.judge_model = required_value(args.judge_model, "--judge-model or FOUNDRY_EVALUATION_MODEL")
    validate_dataset_files(args)

    credential = DefaultAzureCredential()
    with AIProjectClient(
        endpoint=args.project_endpoint,
        credential=credential,
        allow_preview=True,
    ) as project_client:
        with project_client.get_openai_client() as openai_client:
            if args.suite in {"single-turn", "all"}:
                run_single_turn(project_client, openai_client, args)
            if args.suite in {"simulation", "all"}:
                run_simulation(project_client, openai_client, args)


if __name__ == "__main__":
    main()
