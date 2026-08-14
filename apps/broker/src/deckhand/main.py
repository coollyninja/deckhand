import uvicorn

from .api import create_app
from .config import Settings


def run() -> None:
    settings = Settings()
    uvicorn.run(create_app(settings), host=settings.bind_host, port=settings.bind_port)


if __name__ == "__main__":
    run()
