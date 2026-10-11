from mcp_server import text3d_backend


def test_get_backend_tripo(monkeypatch):
    monkeypatch.setenv("TEXT3D_BACKEND", "tripo")
    backend = text3d_backend.get_backend()
    assert isinstance(backend, text3d_backend.TripoBackend)
