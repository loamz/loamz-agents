"""With Loamz Data configured, the server hands agents data tools and the code interpreter a data source that act as the person."""

from loamz.runtime.providers.files import LocalFileStore
from loamz.server.agents.bootstrap import Container, Settings


def test_files_are_a_local_folder_and_data_is_off_without_a_service(tmp_path):
    container = Container(Settings(files={"path": str(tmp_path)}))
    assert isinstance(container.file_store, LocalFileStore)
    assert container.data_broker is None and container.data_tool_set == {}


def test_the_data_tools_exist_only_when_a_service_and_an_exchange_client_are_configured(tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("s3cret\n")
    configured = Container(
        Settings(
            oidc={"issuer": "https://idp.test/realms/x"},
            data={
                "url": "http://data-api:8020",
                "exchange_client_id": "loamz-agents",
                "exchange_client_secret_file": str(secret),
                "workspace": "9cce7e59-e1c0-4f8b-ab20-ff93a79161ca",
            },
        )
    )
    assert set(configured.data_tool_set) == {"data_tables", "data_describe", "data_sql"}
    assert "s3cret" not in repr(configured.data_broker)

    no_client = Container(
        Settings(oidc={"issuer": "https://idp.test/realms/x"}, data={"url": "http://data-api:8020"})
    )
    assert no_client.data_tool_set == {}, (
        "a person's authority cannot be exchanged without this server's own client"
    )
