"""Надёжный клиент официальных HTTP API OpenWeather."""

from __future__ import annotations

from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from weather_bot.models import AirQuality, CurrentWeather, Forecast, ForecastPoint, Location


class WeatherError(RuntimeError):
    """Безопасная для показа пользователю ошибка погодного сервиса."""


class LocationNotFound(WeatherError):
    """OpenWeather не нашёл населённый пункт."""


class OpenWeatherClient:
    BASE_URL = "https://api.openweathermap.org"

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 10.0,
        session: requests.Session | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("OpenWeather API key is required")
        self._api_key = api_key
        self._timeout = timeout
        self._session = session or self._build_session()

    @staticmethod
    def _build_session() -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=0.4,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        session.mount("https://", HTTPAdapter(max_retries=retry))
        session.headers.update({"User-Agent": "vk-weather-bot/1.0"})
        return session

    def _get(self, path: str, **params: Any) -> Any:
        params.update(appid=self._api_key)
        try:
            response = self._session.get(
                f"{self.BASE_URL}{path}", params=params, timeout=self._timeout
            )
        except requests.RequestException as error:
            raise WeatherError(
                "Сервис погоды временно недоступен. Попробуйте чуть позже."
            ) from error

        if response.status_code == 401:
            raise WeatherError("Ключ OpenWeather недействителен или ещё не активирован.")
        if response.status_code == 429:
            raise WeatherError("Лимит запросов OpenWeather исчерпан. Попробуйте позже.")
        if response.status_code >= 400:
            raise WeatherError(
                f"OpenWeather вернул ошибку (код {response.status_code})."
            )
        try:
            data = response.json()
        except (requests.JSONDecodeError, ValueError) as error:
            raise WeatherError("OpenWeather вернул некорректный ответ.") from error
        if not isinstance(data, (dict, list)):
            raise WeatherError("OpenWeather вернул некорректный ответ.")
        return data

    def find_location(self, city: str) -> Location:
        query = " ".join(city.split())
        if not 1 < len(query) <= 100:
            raise LocationNotFound("Введите название города длиной от 2 до 100 символов.")
        data = self._get("/geo/1.0/direct", q=query, limit=1)
        if not isinstance(data, list) or not data:
            raise LocationNotFound(f"Не удалось найти город «{query}».")
        return self._parse_location(data[0])

    def reverse_location(self, latitude: float, longitude: float) -> Location:
        self._validate_coordinates(latitude, longitude)
        data = self._get(
            "/geo/1.0/reverse", lat=latitude, lon=longitude, limit=1
        )
        if isinstance(data, list) and data:
            return self._parse_location(data[0])
        return Location("Точка на карте", "", latitude, longitude)

    def current(self, location: Location) -> CurrentWeather:
        data = self._get(
            "/data/2.5/weather",
            lat=location.latitude,
            lon=location.longitude,
            units="metric",
            lang="ru",
        )
        try:
            main = data["main"]
            wind = data.get("wind", {})
            condition = data["weather"][0]
            system = data.get("sys", {})
            return CurrentWeather(
                location=location,
                measured_at=int(data["dt"]),
                timezone_offset=int(data.get("timezone", 0)),
                condition_id=int(condition["id"]),
                description=str(condition["description"]),
                temperature=float(main["temp"]),
                feels_like=float(main["feels_like"]),
                temp_min=float(main.get("temp_min", main["temp"])),
                temp_max=float(main.get("temp_max", main["temp"])),
                pressure=int(main["pressure"]),
                humidity=int(main["humidity"]),
                visibility=_optional_int(data.get("visibility")),
                wind_speed=float(wind.get("speed", 0)),
                wind_degrees=_optional_int(wind.get("deg")),
                wind_gust=_optional_float(wind.get("gust")),
                clouds=int(data.get("clouds", {}).get("all", 0)),
                rain_1h=float(data.get("rain", {}).get("1h", 0)),
                snow_1h=float(data.get("snow", {}).get("1h", 0)),
                sunrise=_optional_int(system.get("sunrise")),
                sunset=_optional_int(system.get("sunset")),
            )
        except (AttributeError, KeyError, IndexError, TypeError, ValueError) as error:
            raise WeatherError("В ответе OpenWeather не хватает данных о погоде.") from error

    def forecast(self, location: Location) -> Forecast:
        data = self._get(
            "/data/2.5/forecast",
            lat=location.latitude,
            lon=location.longitude,
            units="metric",
            lang="ru",
        )
        try:
            points = tuple(self._parse_forecast_point(item) for item in data["list"])
            timezone_offset = int(data.get("city", {}).get("timezone", 0))
        except (AttributeError, KeyError, IndexError, TypeError, ValueError) as error:
            raise WeatherError("В ответе OpenWeather не хватает данных прогноза.") from error
        if not points:
            raise WeatherError("OpenWeather вернул пустой прогноз.")
        return Forecast(location, timezone_offset, points)

    def air_quality(self, location: Location) -> AirQuality:
        data = self._get(
            "/data/2.5/air_pollution",
            lat=location.latitude,
            lon=location.longitude,
        )
        try:
            item = data["list"][0]
            components = item.get("components", {})
            return AirQuality(
                index=int(item["main"]["aqi"]),
                pm2_5=_optional_float(components.get("pm2_5")),
                pm10=_optional_float(components.get("pm10")),
            )
        except (AttributeError, KeyError, IndexError, TypeError, ValueError) as error:
            raise WeatherError("В ответе OpenWeather не хватает данных о воздухе.") from error

    @staticmethod
    def _parse_location(item: dict[str, Any]) -> Location:
        try:
            local_names = item.get("local_names") or {}
            name = str(local_names.get("ru") or item["name"])
            return Location(
                name=name,
                state=str(item["state"]) if item.get("state") else None,
                country=str(item.get("country", "")),
                latitude=float(item["lat"]),
                longitude=float(item["lon"]),
            )
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise WeatherError("OpenWeather вернул некорректные координаты.") from error

    @staticmethod
    def _parse_forecast_point(item: dict[str, Any]) -> ForecastPoint:
        main = item["main"]
        condition = item["weather"][0]
        return ForecastPoint(
            timestamp=int(item["dt"]),
            condition_id=int(condition["id"]),
            description=str(condition["description"]),
            temperature=float(main["temp"]),
            temp_min=float(main.get("temp_min", main["temp"])),
            temp_max=float(main.get("temp_max", main["temp"])),
            feels_like=float(main["feels_like"]),
            humidity=int(main["humidity"]),
            wind_speed=float(item.get("wind", {}).get("speed", 0)),
            precipitation_probability=float(item.get("pop", 0)),
            rain_3h=float(item.get("rain", {}).get("3h", 0)),
            snow_3h=float(item.get("snow", {}).get("3h", 0)),
        )

    @staticmethod
    def _validate_coordinates(latitude: float, longitude: float) -> None:
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise WeatherError("Получены некорректные координаты.")


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)
