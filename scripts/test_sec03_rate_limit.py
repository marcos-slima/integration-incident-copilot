#!/usr/bin/env uv run
import asyncio
import secrets
import sys
import time

import httpx

BASE_URL = "http://127.0.0.1:8000"
ENDPOINT = "/auth/login"


async def try_login(client, api_key=None):
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key
    payload = {"username": "admin", "password": "qualquer-coisa"}
    try:
        response = await client.post(
            f"{BASE_URL}{ENDPOINT}", json=payload, headers=headers, timeout=5
        )
        return response.status_code
    except httpx.RequestError:
        return 0


async def test_without_api_key():
    print("Test 1: Without X-API-Key")
    async with httpx.AsyncClient() as client:
        successes = 0
        start = time.time()
        for i in range(12):
            status = await try_login(client, api_key=None)
            if status == 200:
                successes += 1
                print(f"  T{i + 1}: 200 OK")
            elif status == 429:
                print(f"  T{i + 1}: 429 Too Many Requests")
                break
            else:
                print(f"  T{i + 1}: {status}")
        elapsed = time.time() - start
        print(f"  Result: {successes} success in {elapsed:.2f}s\n")


async def test_with_any_api_key():
    print("Test 2: With X-API-Key")
    async with httpx.AsyncClient() as client:
        api_key = "any-key-" + secrets.token_hex(8)
        successes = 0
        start = time.time()
        for i in range(12):
            status = await try_login(client, api_key=api_key)
            if status == 200:
                successes += 1
                print(f"  T{i + 1}: 200 OK")
            elif status == 429:
                print(f"  T{i + 1}: 429 Too Many Requests")
                break
            else:
                print(f"  T{i + 1}: {status}")
        elapsed = time.time() - start
        print(f"  Result: {successes} success in {elapsed:.2f}s")
        if successes > 5:
            print("  FAIL: Rate limit bypassed!")
        else:
            print("  SUCCESS: Rate limit working\n")


async def main():
    print("=" * 60)
    print("SEC-03: Rate limit bypass test")
    print("=" * 60)
    print()

    try:
        async with httpx.AsyncClient(timeout=2) as client:
            await client.get(f"{BASE_URL}/health")
    except httpx.RequestError:
        print("ERROR: FastAPI not running at http://127.0.0.1:8000\n")
        return 1

    print("Server detected. Starting tests...\n")

    try:
        await test_without_api_key()
        await test_with_any_api_key()
    except httpx.RequestError as e:
        print(f"Request error: {e}\n")
        return 1

    print("=" * 60)
    print("Test complete")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
