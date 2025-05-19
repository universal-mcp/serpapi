
import httpx
from loguru import logger
from serpapi import SerpApiClient as SerpApiSearch # Added SerpApiError
from typing import Any, Optional # For type hinting

from universal_mcp.applications import APIApplication
from universal_mcp.exceptions import NotAuthorizedError # For auth errors
from universal_mcp.integrations import Integration # For integration type hint


class SerpapiApp(APIApplication):
    def __init__(self, integration: Integration | None = None, **kwargs: Any) -> None:
        super().__init__(name="serpapi", integration=integration, **kwargs)
        self._serpapi_api_key: str | None = None  # Cache for the API key

    @property
    def serpapi_api_key(self) -> str:
        """
        Retrieves and caches the SerpApi API key from the integration.
        Raises NotAuthorizedError if the key cannot be obtained.
        """
        if self._serpapi_api_key is None:
            if not self.integration:
                logger.error("SerpApi App: Integration not configured.")
                raise NotAuthorizedError(
                    "Integration not configured for SerpApi App. Cannot retrieve API key."
                )

            try:
                credentials = self.integration.get_credentials()
            except NotAuthorizedError as e:
                logger.error(f"SerpApi App: Authorization error when fetching credentials: {e.message}")
                raise  # Re-raise the original NotAuthorizedError
            except Exception as e:
                logger.error(f"SerpApi App: Unexpected error when fetching credentials: {e}", exc_info=True)
                raise NotAuthorizedError(f"Failed to get SerpApi credentials: {e}")

            api_key = (
                credentials.get("api_key")
                or credentials.get("API_KEY") # Check common variations
                or credentials.get("apiKey")
            )

            if not api_key:
                logger.error("SerpApi App: API key not found in credentials.")
                action_message = "API key for SerpApi is missing. Please ensure it's set in the store (e.g., SERPAPI_API_KEY in credentials)."
                if hasattr(self.integration, 'authorize') and callable(self.integration.authorize):
                    try:
                        auth_details = self.integration.authorize()
                        if isinstance(auth_details, str):
                            action_message = auth_details
                        elif isinstance(auth_details, dict) and 'url' in auth_details:
                            action_message = f"Please authorize via: {auth_details['url']}"
                        elif isinstance(auth_details, dict) and 'message' in auth_details:
                            action_message = auth_details['message']
                    except Exception as auth_e:
                        logger.warning(f"Could not retrieve specific authorization action for SerpApi: {auth_e}")
                raise NotAuthorizedError(action_message)

            self._serpapi_api_key = api_key
            logger.info("SerpApi API Key successfully retrieved and cached.")
        return self._serpapi_api_key

    async def search(self, params: Optional[dict[str, Any]] = None) -> str:
        """
        Performs a search using the SerpApi service and returns formatted search results.
        Note: The underlying SerpApiSearch().get_dict() call is synchronous.

        Args:
            params: Dictionary of engine-specific parameters (e.g., {'q': 'Coffee', 'engine': 'google_light', 'location': 'Austin, TX'}). Defaults to None.

        Returns:
            A formatted string containing search results with titles, links, and snippets, or an error message if the search fails.

        Raises:
            NotAuthorizedError: If the API key cannot be retrieved or is invalid/rejected by SerpApi.
            Exception: For other unexpected errors during the search process. (Specific HTTP errors or SerpApiErrors are caught and returned as strings or raise NotAuthorizedError).

        Tags:
            search, async, web-scraping, api, serpapi, important
        """
        request_params = params or {}

        try:
            current_api_key = self.serpapi_api_key # This can raise NotAuthorizedError
            logger.info("Attempting SerpApi search.")

            serpapi_call_params = {
                "api_key": current_api_key,
                "engine": "google_light",  # Fastest engine by default
                **request_params,          # Include any additional parameters from the user
            }

            # SerpApiSearch (SerpApiClient) uses the 'requests' library and its get_dict() is synchronous.
            # If true async behavior is needed, this call should be wrapped with asyncio.to_thread.
            search_client = SerpApiSearch(serpapi_call_params)
            data = search_client.get_dict()

            # Check for errors returned in the API response body
            if "error" in data:
                error_message = data["error"]
                logger.error(f"SerpApi API returned an error: {error_message}")
                # Keywords indicating authorization/authentication issues
                auth_error_keywords = ["invalid api key", "authorization failed", "api key needed", "forbidden", "account disabled", "private api key is missing"]
                if any(keyword in error_message.lower() for keyword in auth_error_keywords):
                    raise NotAuthorizedError(f"SerpApi Error: {error_message}")
                return f"SerpApi API Error: {error_message}" # Other API errors (e.g., missing parameters)

            # Process organic search results if available
            if "organic_results" in data:
                formatted_results = []
                for result in data.get("organic_results", []):
                    title = result.get("title", "No title")
                    link = result.get("link", "No link")
                    snippet = result.get("snippet", "No snippet")
                    formatted_results.append(
                        f"Title: {title}\nLink: {link}\nSnippet: {snippet}\n"
                    )
                return (
                    "\n".join(formatted_results)
                    if formatted_results
                    else "No organic results found."
                )
            else:
                return "No organic results found."

        except NotAuthorizedError: # Catches from self.serpapi_api_key or explicit raise above
            logger.error("SerpApi search failed due to an authorization error.")
            raise # Re-raise to be handled by the MCP framework

        except httpx.HTTPStatusError as e: # Kept from original for robustness, though SerpApiClient uses 'requests'
            logger.warning(f"SerpApi search encountered httpx.HTTPStatusError (unexpected with default SerpApiClient): {e.response.status_code}", exc_info=True)
            if e.response.status_code == 429:
                return "Error: Rate limit exceeded (HTTP 429). Please try again later."
            elif e.response.status_code == 401: # Key was fetched but rejected by API with HTTP 401
                raise NotAuthorizedError("Error: Invalid API key (HTTP 401). Please check your SERPAPI_API_KEY.")
            else:
                return f"HTTP Error: {e.response.status_code} - {e.response.text}"

        except Exception as e: # General catch-all, similar to E2B's final catch
            error_message_lower = str(e).lower()
            logger.error(f"Unexpected error during SerpApi search: {e}", exc_info=True)
            # Infer auth error from generic exception message
            auth_error_keywords = ["authentication", "api key", "unauthorized", "401", "forbidden", "invalid key"]
            if any(keyword in error_message_lower for keyword in auth_error_keywords):
                raise NotAuthorizedError(f"SerpApi authentication/authorization failed: {str(e)}")
            return f"An unexpected error occurred during search: {str(e)}"

    def list_tools(self) -> list[callable]:
        return [
            self.search,
        ]
