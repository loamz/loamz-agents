"""Loamz's key-free look-up tools: weather, currency rates, research papers and software packages. Each reads from one fixed public
service, so the model never chooses the address, and each only reads."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from loamz.runtime.kernel.tools import ToolExecutionResult, ToolRisk
from loamz.runtime.kernel.types.content import TextBlock

TIMEOUT = 12.0
AGENT = "LoamzBot/1.0 (+https://loamz.com)"
WEATHER = {
    0: "clear",
    1: "mostly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "freezing fog",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    66: "freezing rain",
    67: "heavy freezing rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    77: "snow grains",
    80: "light showers",
    81: "showers",
    82: "heavy showers",
    85: "snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "thunderstorm with heavy hail",
}


class Lookup:
    """A tool that reads one public JSON or text service. `client` is replaced in tests."""

    risk = ToolRisk.SAFE
    idempotent = True
    name = ""
    description = ""
    input_schema: dict[str, object] = {}

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def get(self, url: str, **params: Any) -> httpx.Response:
        client = self._client or httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": AGENT})
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response
        finally:
            if self._client is None:
                await client.aclose()

    async def execute(self, *, ctx: object = None, **kwargs: Any) -> ToolExecutionResult:
        kwargs.pop("call_id", None)
        try:
            return ToolExecutionResult(content=[TextBlock(text=await self.run(**kwargs))])
        except LookupError as why:
            return ToolExecutionResult(content=[TextBlock(text=str(why))], is_error=True)
        except httpx.HTTPStatusError as exc:
            note = (
                "Nothing was found for that."
                if exc.response.status_code == 404
                else f"The service answered {exc.response.status_code}."
            )
            return ToolExecutionResult(content=[TextBlock(text=note)], is_error=True)
        except httpx.HTTPError, ValueError, KeyError, ET.ParseError:
            return ToolExecutionResult(
                content=[
                    TextBlock(
                        text="The service could not be reached or gave an odd answer. Try again shortly."
                    )
                ],
                is_error=True,
            )

    async def run(self, **kwargs: Any) -> str:
        raise NotImplementedError


class WeatherTool(Lookup):
    name = "weather"
    description = (
        "Current weather and a three-day forecast for a place, from Open-Meteo. Pass the place name, such as 'Hyderabad' or "
        "'Paris, France'. Temperatures are in °C and wind in km/h."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"place": {"type": "string", "description": "A city or town."}},
        "required": ["place"],
        "additionalProperties": False,
    }

    async def run(self, place: str = "", **_: Any) -> str:
        name = place.split(",")[0].strip()
        if not name:
            raise LookupError("Say which place.")
        found = (
            await self.get(
                "https://geocoding-api.open-meteo.com/v1/search", name=name, count=1, language="en"
            )
        ).json().get("results") or []
        if not found:
            raise LookupError(f"No place called '{name}' was found.")
        spot = found[0]
        data = (
            await self.get(
                "https://api.open-meteo.com/v1/forecast",
                latitude=spot["latitude"],
                longitude=spot["longitude"],
                current="temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,precipitation,weather_code",
                daily="weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum",
                forecast_days=3,
                timezone="auto",
            )
        ).json()
        now, day = data["current"], data["daily"]
        where = ", ".join(x for x in (spot["name"], spot.get("admin1"), spot.get("country")) if x)
        lines = [
            f"{where}: {now['temperature_2m']}°C (feels {now['apparent_temperature']}°C), "
            f"{WEATHER.get(now['weather_code'], 'unknown conditions')}, humidity {now['relative_humidity_2m']}%, "
            f"wind {now['wind_speed_10m']} km/h, rain now {now['precipitation']} mm.",
            "Forecast:",
        ]
        for i, date in enumerate(day["time"]):
            lines.append(
                f"- {date}: {day['temperature_2m_min'][i]}–{day['temperature_2m_max'][i]}°C, "
                f"{WEATHER.get(day['weather_code'][i], 'unknown')}, rain {day['precipitation_sum'][i]} mm"
            )
        return "\n".join(lines)


class CurrencyTool(Lookup):
    name = "currency"
    description = (
        "Exchange rates and conversions between currencies (European Central Bank reference rates, updated each working day). "
        "Pass an amount and 3-letter codes such as USD, EUR, INR; add a date to use that day's rate."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "amount": {"type": "number", "description": "How much to convert. Default 1."},
            "from": {"type": "string", "description": "Currency code to convert from, like USD."},
            "to": {"type": "string", "description": "Currency code to convert to, like EUR."},
            "date": {"type": "string", "description": "Optional day, YYYY-MM-DD."},
        },
        "required": ["from", "to"],
        "additionalProperties": False,
    }

    async def run(self, amount: float = 1, to: str = "", date: str = "", **kw: Any) -> str:
        base = str(kw.get("from", "")).upper()
        target = to.upper()
        if not (re.fullmatch(r"[A-Z]{3}", base) and re.fullmatch(r"[A-Z]{3}", target)):
            raise LookupError("Use 3-letter currency codes, like USD and EUR.")
        if date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise LookupError("Give the date as YYYY-MM-DD.")
        data = (
            await self.get(
                f"https://api.frankfurter.dev/v1/{date or 'latest'}",
                base=base,
                symbols=target,
                amount=amount,
            )
        ).json()
        if target not in data["rates"]:
            raise LookupError(f"No rate for {base} to {target}.")
        return f"{amount:g} {base} = {data['rates'][target]:g} {target} (rate of {data['date']}, ECB reference)."


class PapersTool(Lookup):
    name = "papers"
    description = (
        "Search research papers on arXiv (physics, maths, computer science, statistics, biology and more). Returns the title, "
        "authors, date, link and the start of the abstract. Use it for what the research says, then read a paper with read_page."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Topic or title words."},
            "count": {"type": "integer", "description": "How many, 1 to 8. Default 5."},
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    async def run(self, query: str = "", count: int = 5, **_: Any) -> str:
        words = re.sub(r"[^\w\s-]", " ", query).split()
        if not words:
            raise LookupError("Say what to search for.")
        body = (
            await self.get(
                "https://export.arxiv.org/api/query",
                search_query="all:" + "+AND+".join(words[:8]),
                max_results=max(1, min(int(count), 8)),
                sortBy="relevance",
            )
        ).text
        if "<!DOCTYPE" in body or "<!ENTITY" in body:
            raise ValueError("unexpected markup")
        ns = {"a": "http://www.w3.org/2005/Atom"}
        entries = ET.fromstring(body).findall("a:entry", ns)
        if not entries:
            return "No papers matched. Try fewer or different words."
        out = []
        for e in entries:

            def text(tag: str, e: ET.Element = e) -> str:
                return re.sub(r"\s+", " ", e.findtext(f"a:{tag}", "", ns)).strip()

            authors = [a.findtext("a:name", "", ns) for a in e.findall("a:author", ns)]
            who = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
            out.append(
                f"**{text('title')}**\n{who} · {text('published')[:10]}\n{text('id')}\n{text('summary')[:500]}"
            )
        return "\n\n".join(out)


class PackagesTool(Lookup):
    name = "packages"
    description = (
        "Look up a software package: its newest version, what it does, licence and links. Ecosystems: pypi (Python), npm "
        "(JavaScript) and crates (Rust). Use it instead of guessing a version number."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "ecosystem": {"type": "string", "enum": ["pypi", "npm", "crates"]},
            "name": {"type": "string", "description": "The package name, like requests or react."},
        },
        "required": ["ecosystem", "name"],
        "additionalProperties": False,
    }

    async def run(self, ecosystem: str = "", name: str = "", **_: Any) -> str:
        if not re.fullmatch(r"(@[a-z0-9._-]+/)?[A-Za-z0-9._-]{1,100}", name):
            raise LookupError("That is not a package name.")
        if ecosystem == "pypi":
            info = (await self.get(f"https://pypi.org/pypi/{name}/json")).json()["info"]
            return self._card(
                info["name"],
                info["version"],
                info.get("summary"),
                info.get("license_expression") or info.get("license"),
                info.get("home_page") or (info.get("project_urls") or {}).get("Homepage"),
                f"https://pypi.org/project/{name}/",
                f"pip install {info['name']}",
            )
        if ecosystem == "npm":
            path = name.replace("/", "%2F")
            info = (await self.get(f"https://registry.npmjs.org/{path}/latest")).json()
            return self._card(
                info["name"],
                info["version"],
                info.get("description"),
                info.get("license"),
                info.get("homepage"),
                f"https://www.npmjs.com/package/{name}",
                f"npm install {info['name']}",
            )
        if ecosystem == "crates":
            crate = (await self.get(f"https://crates.io/api/v1/crates/{name}")).json()["crate"]
            return self._card(
                crate["name"],
                crate["max_stable_version"] or crate["newest_version"],
                crate.get("description"),
                None,
                crate.get("homepage") or crate.get("repository"),
                f"https://crates.io/crates/{name}",
                f"cargo add {crate['name']}",
            )
        raise LookupError("Ecosystem must be pypi, npm or crates.")

    @staticmethod
    def _card(name, version, summary, licence, home, page, install) -> str:  # noqa: ANN001
        rows = [
            f"{name} {version}",
            summary or "",
            f"Licence: {licence}" if licence else "",
            home or "",
            page,
            install,
        ]
        return "\n".join(r for r in rows if r)
