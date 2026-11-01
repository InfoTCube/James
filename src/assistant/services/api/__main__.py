import uvicorn

# graceful shutdown waits for open requests: the voice event stream would hold it for minutes
uvicorn.run(
    "assistant.services.api.app:app", host="0.0.0.0", port=8000, timeout_graceful_shutdown=3
)
