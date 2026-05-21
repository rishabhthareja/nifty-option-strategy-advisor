import os
from config import LANGCHAIN_API_KEY, LANGCHAIN_PROJECT, LANGCHAIN_TRACING_V2


def save_to_langsmith_dataset(
    dataset_name: str,
    inputs: dict,
    outputs: dict,
    run_id: str,
):
    """Save evaluation to LangSmith dataset."""
    if LANGCHAIN_TRACING_V2.lower() != "true" or not LANGCHAIN_API_KEY:
        return

    try:
        from langsmith import Client

        client = Client(api_key=LANGCHAIN_API_KEY)

        datasets = list(client.list_datasets(dataset_name=dataset_name))
        if not datasets:
            dataset = client.create_dataset(
                dataset_name=dataset_name,
                description="Nifty options strategy evaluations",
            )
        else:
            dataset = datasets[0]

        client.create_example(
            inputs=inputs,
            outputs=outputs,
            dataset_id=dataset.id,
            metadata={"run_id": run_id},
        )
    except Exception as e:
        print(f"[LangSmith] Failed to save to dataset: {e}")
