import logging
import os
import aiohttp

logger = logging.getLogger(__name__)

async def perform_web_search(query: str, max_results: int = 5) -> list[dict]:
    """
    Search the web using Tavily API.
    
    Args:
        query: The search query string.
        max_results: Number of results to return.
        
    Returns:
        A list of result dictionaries: [{"title": str, "url": str, "content": str, "score": float}]
    """
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        logger.warning("TAVILY_API_KEY not set. Web search will return mock/empty results.")
        # Fallback for dev mode
        return [{"title": "Web Search Disabled", "url": "http://localhost", "content": "Tavily API key is missing. Please add it to your .env file to enable live web search.", "score": 1.0}]

    try:
        async with aiohttp.ClientSession() as session:
            url = "https://api.tavily.com/search"
            payload = {
                "api_key": api_key,
                "query": query,
                "search_depth": "basic",
                "max_results": max_results,
                "include_answer": False,
                "time_range": "year",
            }
            async with session.post(url, json=payload) as response:
                if response.status != 200:
                    text = await response.text()
                    logger.error("Tavily search failed: %s", text)
                    return []
                
                data = await response.json()
                results = []
                for item in data.get("results", []):
                    results.append({
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "content": item.get("content", ""),
                        "score": item.get("score", 0.0)
                    })
                return results
    except Exception as e:
        logger.error("Error performing web search: %s", e)
        return []
