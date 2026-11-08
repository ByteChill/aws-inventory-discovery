from typing import Annotated

from fastapi import Depends
from sqlmodel import Session

from app.core.db import get_read_session, get_session

# Writes (scan requests, guard confirmation) go to the primary.
SessionDep = Annotated[Session, Depends(get_session)]
# Queries and exports read from DATABASE_READ_URL (falls back to DATABASE_URL).
ReadSessionDep = Annotated[Session, Depends(get_read_session)]
