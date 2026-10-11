"""Create optional WorkPilot managed Google Cloud resources.

Run after APIs are enabled and ADC is configured:
  python scripts/bootstrap_cloud.py

Creates:
- Document AI OCR processor
- Agent Search data store + search engine
- BigQuery dataset + events table

The script is idempotent where practical and prints the environment variables to
add to Cloud Run. Search resources can take several minutes to provision.
"""
import os
from google.api_core.client_options import ClientOptions
from google.api_core.exceptions import NotFound

PROJECT = os.environ["GOOGLE_CLOUD_PROJECT"]
DOC_LOCATION = os.getenv("DOCUMENT_AI_LOCATION", "us")
SEARCH_LOCATION = os.getenv("VERTEX_SEARCH_LOCATION", "global")
DATA_STORE_ID = os.getenv("VERTEX_SEARCH_DATA_STORE_ID", "workpilot-knowledge")
ENGINE_ID = os.getenv("VERTEX_SEARCH_ENGINE_ID", "workpilot-search")
BQ_DATASET = os.getenv("BIGQUERY_DATASET", "workpilot")
BQ_TABLE = os.getenv("BIGQUERY_TABLE", "events")


def create_document_ai_processor():
    from google.cloud import documentai_v1 as documentai
    opts = ClientOptions(api_endpoint=f"{DOC_LOCATION}-documentai.googleapis.com")
    client = documentai.DocumentProcessorServiceClient(client_options=opts)
    parent = f"projects/{PROJECT}/locations/{DOC_LOCATION}"
    for proc in client.list_processors(parent=parent):
        if proc.display_name == "WorkPilot OCR":
            return proc.name.rsplit("/", 1)[-1]
    proc = documentai.Processor(type_="OCR_PROCESSOR", display_name="WorkPilot OCR")
    created = client.create_processor(parent=parent, processor=proc)
    return created.name.rsplit("/", 1)[-1]


def search_options():
    return None if SEARCH_LOCATION == "global" else ClientOptions(api_endpoint=f"{SEARCH_LOCATION}-discoveryengine.googleapis.com")


def create_agent_search():
    from google.cloud import discoveryengine_v1 as de
    opts = search_options()
    ds_client = de.DataStoreServiceClient(client_options=opts)
    collection = ds_client.collection_path(PROJECT, SEARCH_LOCATION, "default_collection")
    ds_name = f"{collection}/dataStores/{DATA_STORE_ID}"
    try:
        ds_client.get_data_store(name=ds_name)
    except NotFound:
        data_store = de.DataStore(
            display_name="WorkPilot Enterprise Knowledge",
            industry_vertical=de.IndustryVertical.GENERIC,
            solution_types=[de.SolutionType.SOLUTION_TYPE_SEARCH],
            content_config=de.DataStore.ContentConfig.CONTENT_REQUIRED,
        )
        op = ds_client.create_data_store(
            parent=collection,
            data_store=data_store,
            data_store_id=DATA_STORE_ID,
        )
        op.result(timeout=600)

    engine_client = de.EngineServiceClient(client_options=opts)
    engine_name = f"{collection}/engines/{ENGINE_ID}"
    try:
        engine_client.get_engine(name=engine_name)
    except NotFound:
        engine = de.Engine(
            display_name="WorkPilot Agent Search",
            industry_vertical=de.IndustryVertical.GENERIC,
            solution_type=de.SolutionType.SOLUTION_TYPE_SEARCH,
            data_store_ids=[DATA_STORE_ID],
            search_engine_config=de.Engine.SearchEngineConfig(
                search_tier=de.SearchTier.SEARCH_TIER_ENTERPRISE,
                search_add_ons=[de.SearchAddOn.SEARCH_ADD_ON_LLM],
            ),
        )
        op = engine_client.create_engine(parent=collection, engine=engine, engine_id=ENGINE_ID)
        op.result(timeout=1200)


def create_bigquery():
    from google.cloud import bigquery
    client = bigquery.Client(project=PROJECT)
    dataset = bigquery.Dataset(f"{PROJECT}.{BQ_DATASET}")
    dataset.location = "US"
    client.create_dataset(dataset, exists_ok=True)
    table_id = f"{PROJECT}.{BQ_DATASET}.{BQ_TABLE}"
    table = bigquery.Table(table_id, schema=[
        bigquery.SchemaField("event_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("event_type", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("workspace_id", "STRING"),
        bigquery.SchemaField("decision_id", "STRING"),
        bigquery.SchemaField("action_id", "STRING"),
        bigquery.SchemaField("source_count", "INTEGER"),
        bigquery.SchemaField("duration_ms", "INTEGER"),
        bigquery.SchemaField("estimated_minutes_saved", "FLOAT"),
        bigquery.SchemaField("timestamp", "TIMESTAMP", mode="REQUIRED"),
        bigquery.SchemaField("metadata", "JSON"),
    ])
    client.create_table(table, exists_ok=True)


if __name__ == "__main__":
    print(f"Project: {PROJECT}")
    processor_id = create_document_ai_processor()
    print(f"Document AI processor: {processor_id}")
    create_agent_search()
    print(f"Agent Search: data_store={DATA_STORE_ID}, engine={ENGINE_ID}")
    create_bigquery()
    print(f"BigQuery: {BQ_DATASET}.{BQ_TABLE}")
    print("\nCloud Run environment variables:")
    print("DOCUMENT_AI_ENABLED=true")
    print(f"DOCUMENT_AI_LOCATION={DOC_LOCATION}")
    print(f"DOCUMENT_AI_PROCESSOR_ID={processor_id}")
    print("VERTEX_SEARCH_ENABLED=true")
    print(f"VERTEX_SEARCH_LOCATION={SEARCH_LOCATION}")
    print(f"VERTEX_SEARCH_DATA_STORE_ID={DATA_STORE_ID}")
    print(f"VERTEX_SEARCH_ENGINE_ID={ENGINE_ID}")
    print("ADK_ENABLED=true")
    print("BIGQUERY_ENABLED=true")
    print(f"BIGQUERY_DATASET={BQ_DATASET}")
    print(f"BIGQUERY_TABLE={BQ_TABLE}")
