"""
LLM service.
Wraps OpenRouter API calls and robust JSON parsing.
Kept separate from AnalysisService to avoid circular imports.
"""
import json
import requests
from typing import Dict, Any, Optional, List

from app.utils.logger import get_logger
from app.config import APIKeys
from app.utils.config_loader import load_addon_config

logger = get_logger(__name__)


class LLMService:
    """LLM provider wrapper."""

    def __init__(self):
        # Config may not be loaded yet during import time; we resolve lazily via properties.
        pass

    @property
    def api_key(self):
        return APIKeys.OPENROUTER_API_KEY

    @property
    def base_url(self):
        config = load_addon_config()
        # Keep compatible with old/new config keys.
        import os
        return config.get('openrouter', {}).get('base_url') or os.getenv('OPENROUTER_BASE_URL', "https://openrouter.ai/api/v1")

    def call_openrouter_api(self, messages: list, model: str = None, temperature: float = 0.7, use_fallback: bool = True) -> str:
        """Call OpenRouter API, with optional fallback models."""
        config = load_addon_config()
        openrouter_config = config.get('openrouter', {})
        
        default_model = openrouter_config.get('model', 'openai/gpt-4o')
        
        if model is None:
            model = default_model
            
        url = f"{self.base_url}/chat/completions"
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://quantdinger.com", 
            "X-Title": "QuantDinger Analysis" 
        }

        # Build model candidates (primary + optional fallbacks).
        models_to_try = [model]
        
        # Fallback models are currently hard-coded for local mode.
        fallback_models = ["openai/gpt-4o-mini"]
        
        if use_fallback and model == default_model:
            models_to_try.extend(fallback_models)

        last_error = None
        
        timeout = int(openrouter_config.get('timeout', 120))
        
        for current_model in models_to_try:
            try:
                data = {
                    "model": current_model,
                    "messages": messages,
                    "temperature": temperature,
                    "response_format": {"type": "json_object"}
                }
                # logger.debug(f"Trying model: {current_model}")

                response = requests.post(url, headers=headers, json=data, timeout=timeout)
                
                if response.status_code == 402:
                    logger.warning(f"OpenRouter returned 402 for model {current_model}; trying fallback model...")
                    last_error = f"402 Payment Required for model {current_model}"
                    continue
                
                response.raise_for_status()
                
                result = response.json()
                if "choices" in result and len(result["choices"]) > 0:
                    content = result["choices"][0]["message"]["content"]
                    if not content:
                        raise ValueError(f"Model {current_model} returned empty content")
                        
                    if current_model != model:
                        logger.info(f"Fallback model succeeded: {current_model}")
                    return content
                else:
                    logger.error(f"OpenRouter API returned unexpected structure ({current_model}): {json.dumps(result)}")
                    raise ValueError("OpenRouter API response is missing 'choices'")
                    
            except requests.exceptions.HTTPError as e:
                logger.error(f"OpenRouter API HTTP error ({current_model}): {e.response.text if e.response else str(e)}")
                last_error = str(e)
                if not use_fallback or current_model == models_to_try[-1]:
                    raise
            except requests.exceptions.RequestException as e:
                logger.error(f"OpenRouter API request error ({current_model}): {str(e)}")
                last_error = str(e)
                if not use_fallback or current_model == models_to_try[-1]:
                    raise
            except ValueError as e:
                logger.warning(f"Model {current_model} returned invalid data: {str(e)}")
                last_error = str(e)
                # If this is not the last candidate model, try the next one
                if current_model == models_to_try[-1]:
                    raise
        
        error_msg = f"All model calls failed. Last error: {last_error}"
        logger.error(error_msg)
        raise Exception(error_msg)

    def safe_call_llm(self, system_prompt: str, user_prompt: str, default_structure: Dict[str, Any], model: str = None) -> Dict[str, Any]:
        """Safe LLM call with robust JSON parsing and fallback structure."""
        response_text = ""
        try:
            response_text = self.call_openrouter_api([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ], model=model)
            
            # Strip markdown fences if present
            clean_text = response_text.strip()
            if clean_text.startswith("```"):
                first_newline = clean_text.find("\n")
                if first_newline != -1:
                    clean_text = clean_text[first_newline+1:]
                if clean_text.endswith("```"):
                    clean_text = clean_text[:-3]
            clean_text = clean_text.strip()
            
            # Parse JSON
            result = json.loads(clean_text)
            return result
        except json.JSONDecodeError:
            logger.error(f"JSON parse failed. Raw text: {response_text[:200] if response_text else 'N/A'}")
            
            # Try extracting JSON substring
            try:
                if response_text:
                    start = response_text.find('{')
                    end = response_text.rfind('}') + 1
                    if start >= 0 and end > start:
                        result = json.loads(response_text[start:end])
                        return result
            except:
                pass
            
            default_structure['report'] = f"Failed to parse analysis result JSON. Raw output (partial): {response_text[:500] if response_text else 'N/A'}"
            return default_structure
        except Exception as e:
            logger.error(f"LLM call failed: {str(e)}")
            default_structure['report'] = f"Analysis failed: {str(e)}"
            return default_structure
