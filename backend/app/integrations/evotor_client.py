"""Минимальный клиент чтения документов из официального API Облака Эвотор."""
from typing import Any
import httpx


class EvotorAPIError(RuntimeError):
    pass


class EvotorClient:
    BASE_URL = "https://api.evotor.ru"

    def __init__(self, token: str):
        if not token:
            raise ValueError("EVOTOR_TOKEN не задан")
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={
                "Authorization": token,
                "Accept": "application/vnd.evotor.v2+json",
                "Content-Type": "application/vnd.evotor.v2+json",
            },
            timeout=httpx.Timeout(30.0, connect=10.0),
        )

    async def get_sales_documents(
        self,
        evotor_store_id: str,
        since_ms: int,
    ) -> list[dict[str, Any]]:
        """Читает документы SELL/PAYBACK с корректной cursor-пагинацией."""
        result: list[dict[str, Any]] = []
        cursor: str | None = None

        while True:
            params: dict[str, str | int] = {"type": "SELL,PAYBACK"}
            if cursor:
                # since/until используются только в первом запросе страницы.
                params = {"cursor": cursor}
            else:
                params["since"] = since_ms

            response = await self._client.get(
                f"/stores/{evotor_store_id}/documents",
                params=params,
            )
            if response.is_error:
                message = response.text[:500]
                raise EvotorAPIError(
                    f"Эвотор API вернул HTTP {response.status_code}: {message}"
                )

            payload = response.json()
            items = payload.get("items", [])
            if not isinstance(items, list):
                raise EvotorAPIError("Неожиданный формат ответа Эвотора: items не список")
            result.extend(item for item in items if isinstance(item, dict))

            cursor = (payload.get("paging") or {}).get("next_cursor")
            if not cursor:
                break

        return result

    async def close(self) -> None:
        await self._client.aclose()
