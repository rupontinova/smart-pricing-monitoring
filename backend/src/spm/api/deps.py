"""FastAPI dependencies shared by routers."""

from typing import Annotated, cast

from fastapi import Depends, Request

from spm.config import Settings
from spm.container import Container


def get_container(request: Request) -> Container:
    return cast(Container, request.app.state.container)


def get_settings_dep(container: Annotated[Container, Depends(get_container)]) -> Settings:
    return container.settings


ContainerDep = Annotated[Container, Depends(get_container)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
