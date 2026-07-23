import sqlite3
from typing import List

from fastapi import APIRouter, HTTPException

greviews_routes = APIRouter(prefix="/google_reviews", tags=["G Reviews"])

DB_PATH = "../static/reviews.db"  # path to your sqlite file


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # lets us return rows as dicts
    return conn


@greviews_routes.get("/")
def get_all_reviews(limit: int = 50, offset: int = 0):
    """Get all reviews from the 'reviews' table."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM reviews LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
    except sqlite3.OperationalError as e:
        raise HTTPException(status_code=404, detail=str(e))
    finally:
        conn.close()

    return {"count": len(rows), "data": [dict(r) for r in rows]}


@greviews_routes.get("/{review_id}")
def get_review_by_id(review_id: int):
    """Get a single review by its id."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM reviews WHERE id = ?", (review_id,)
        ).fetchone()
    except sqlite3.OperationalError as e:
        raise HTTPException(status_code=404, detail=str(e))
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Review not found")

    return dict(row)
