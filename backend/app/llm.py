"""Gemini on Vertex AI: a thin client with no knowledge of the rest of the app."""
from __future__ import annotations

import json


class GeminiClient:
    def __init__(self, project: str, location: str, model: str):
        if not project:
            raise RuntimeError("GOOGLE_CLOUD_PROJECT is not set")
        from google import genai  # optional dependency, imported only when used

        self._client = genai.Client(vertexai=True, project=project, location=location)
        self.model = model

    def generate_json(self, prompt: str) -> object:
        from google.genai import types

        response = self._client.models.generate_content(
            model=self.model, contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0))
        return json.loads(response.text)
