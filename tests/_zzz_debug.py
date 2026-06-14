import pytest
from unittest.mock import AsyncMock, MagicMock, patch

@pytest.mark.asyncio
async def test_debug(client, admin_token):
    r = await client.post("/api/v1/admin/providers", json={
        "name": "debug_w", "display_name": "x", "provider_type": "openai_compat",
        "api_base_url": "https://api.w.com/v1", "api_key": "sk-test"
    }, headers={"Authorization": f"Bearer {admin_token}"})
    pid = r.json()["id"]

    # 直接 mock 整个 httpx.AsyncClient 类
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json = MagicMock(return_value={"choices": [{"message": {"content": "hi"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
    mock_resp.text = ""
    
    mock_client_instance = MagicMock()
    mock_client_instance.post = AsyncMock(return_value=mock_resp)
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=False)
    
    with patch("httpx.AsyncClient", return_value=mock_client_instance):
        r2 = await client.post("/api/v1/admin/models/test", json={
            "provider_id": pid, "model_name": "gpt", "test_message": "hi"
        }, headers={"Authorization": f"Bearer {admin_token}"})
    print(f"\n[DEBUG] mock-class status={r2.status_code}")
    print(f"[DEBUG] mock-class text={r2.text[:500]!r}")
