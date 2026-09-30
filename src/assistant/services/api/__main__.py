import uvicorn

uvicorn.run("assistant.services.api.app:app", host="0.0.0.0", port=8000)
