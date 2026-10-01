from __future__ import annotations

import unittest
from typing import Any

import requests

from weather_bot.openweather import OpenWeatherClient, WeatherError


class FakeSession:
    def __init__(self, responses: list[requests.Response]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def response(status: int, body: str) -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result._content = body.encode("utf-8")
    return result


class OpenWeatherClientTests(unittest.TestCase):
    def test_malformed_location_is_converted_to_safe_error(self) -> None:
        for body in ('[null]', '[42]', '[{"local_names":42}]', 'null'):
            with self.subTest(body=body):
                session = FakeSession([response(200, body)])
                client = OpenWeatherClient("key", session=session)
                with self.assertRaises(WeatherError):
                    client.find_location("Москва")

    def test_find_location_prefers_russian_local_name(self) -> None:
        session = FakeSession(
            [
                response(
                    200,
                    '[{"name":"Moscow","local_names":{"ru":"Москва"},'
                    '"country":"RU","lat":55.75,"lon":37.62}]',
                )
            ]
        )
        client = OpenWeatherClient("key", session=session)  # type: ignore[arg-type]
        location = client.find_location("  Москва  ")

        self.assertEqual(location.name, "Москва")
        _, request = session.calls[0]
        self.assertEqual(request["params"]["q"], "Москва")
        self.assertEqual(request["params"]["appid"], "key")

    def test_auth_error_is_converted_to_safe_message(self) -> None:
        session = FakeSession([response(401, '{}')])
        client = OpenWeatherClient("bad-key", session=session)  # type: ignore[arg-type]
        with self.assertRaisesRegex(WeatherError, "недействителен"):
            client.find_location("Москва")


if __name__ == "__main__":
    unittest.main()
