"""Explicit Uvicorn loop factory for Psycopg on Windows."""
import asyncio
import selectors

def selector_loop():
    return asyncio.SelectorEventLoop(selectors.SelectSelector())
