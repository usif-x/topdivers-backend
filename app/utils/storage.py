"""
Centralized S3/MinIO storage utility.

All file uploads (images, videos, blog assets, gallery) go through this module.
It provides a single S3Client instance and helper functions used by the rest of
the application.
"""

import io
import logging
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException, UploadFile
from PIL import Image

from app.core.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}

MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_VIDEO_SIZE = 100 * 1024 * 1024  # 100 MB

# Content-type mapping for common extensions
CONTENT_TYPE_MAP = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
}


# ---------------------------------------------------------------------------
# S3 Client (singleton)
# ---------------------------------------------------------------------------
def _build_s3_client():
    """Create and return a boto3 S3 client configured for MinIO."""
    return boto3.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        region_name=settings.S3_REGION,
    )


_s3_client = None


def get_s3_client():
    """Return the singleton S3 client, creating it on first call."""
    global _s3_client
    if _s3_client is None:
        _s3_client = _build_s3_client()
        _ensure_bucket_exists()
    return _s3_client


def _ensure_bucket_exists():
    """Create the bucket if it does not already exist."""
    client = _s3_client
    bucket = settings.S3_BUCKET_NAME
    try:
        client.head_bucket(Bucket=bucket)
        logger.info(f"S3 bucket '{bucket}' already exists")
    except ClientError:
        try:
            client.create_bucket(Bucket=bucket)
            # Set bucket policy to allow public read (for serving files)
            policy = (
                '{"Version":"2012-10-17","Statement":[{"Effect":"Allow",'
                '"Principal":"*","Action":"s3:GetObject",'
                f'"Resource":"arn:aws:s3:::{bucket}/*"'
                "}]}"
            )
            client.put_bucket_policy(Bucket=bucket, Policy=policy)
            logger.info(f"S3 bucket '{bucket}' created with public-read policy")
        except (ClientError, BotoCoreError) as exc:
            logger.error(f"Failed to create S3 bucket '{bucket}': {exc}")
            raise


# ---------------------------------------------------------------------------
# Public URL helper
# ---------------------------------------------------------------------------
def get_public_url(object_key: str) -> str:
    """
    Return the public URL for an object stored in S3/MinIO.

    If S3_PUBLIC_URL is configured (recommended), it is used as the base.
    Otherwise, falls back to ``{S3_ENDPOINT_URL}/{S3_BUCKET_NAME}``.
    """
    if object_key.startswith(("http://", "https://")):
        extracted_key = extract_object_key_from_url(object_key)
        if extracted_key:
            object_key = extracted_key
        else:
            # Preserve the object path when the URL belongs to an old public host.
            object_key = urlsplit(object_key).path.lstrip("/")
            bucket_prefix = f"{settings.S3_BUCKET_NAME}/"
            if object_key.startswith(bucket_prefix):
                object_key = object_key[len(bucket_prefix) :]

    base = (
        settings.S3_PUBLIC_URL.rstrip("/")
        if settings.S3_PUBLIC_URL
        else (f"{settings.S3_ENDPOINT_URL.rstrip('/')}/{settings.S3_BUCKET_NAME}")
    )
    return f"{base}/{object_key}"


# ---------------------------------------------------------------------------
# Core upload / delete helpers
# ---------------------------------------------------------------------------
def _upload_bytes_to_s3(
    data: bytes,
    object_key: str,
    content_type: str = "application/octet-stream",
) -> str:
    """
    Upload raw bytes to S3 and return the public URL.

    Args:
        data: File content as bytes.
        object_key: The key (path) inside the bucket.
        content_type: MIME type of the file.

    Returns:
        Public URL of the uploaded object.
    """
    client = get_s3_client()
    try:
        client.put_object(
            Bucket=settings.S3_BUCKET_NAME,
            Key=object_key,
            Body=data,
            ContentType=content_type,
        )
        url = get_public_url(object_key)
        logger.debug(f"Uploaded {object_key} to S3 ({len(data)} bytes)")
        return url
    except (ClientError, BotoCoreError) as exc:
        logger.error(f"S3 upload failed for {object_key}: {exc}")
        raise HTTPException(status_code=500, detail=f"Storage upload failed: {exc}")


def delete_from_s3(object_key: str) -> bool:
    """
    Delete an object from S3.

    Args:
        object_key: The key (path) of the object to delete.

    Returns:
        True if the delete call succeeded.
    """
    client = get_s3_client()
    try:
        client.delete_object(Bucket=settings.S3_BUCKET_NAME, Key=object_key)
        logger.debug(f"Deleted {object_key} from S3")
        return True
    except (ClientError, BotoCoreError) as exc:
        logger.error(f"S3 delete failed for {object_key}: {exc}")
        return False


# ---------------------------------------------------------------------------
# Filename / key generation
# ---------------------------------------------------------------------------
def _generate_object_key(prefix: str, original_filename: str) -> tuple[str, str]:
    """
    Generate a unique S3 object key.

    Args:
        prefix: Folder prefix inside the bucket (e.g. "images", "videos", "blogs").
        original_filename: The original upload filename (used for extension).

    Returns:
        Tuple of (object_key, file_extension).
    """
    ext = Path(original_filename).suffix.lower()
    unique_id = str(uuid.uuid4())
    object_key = f"{prefix}/{unique_id}{ext}"
    return object_key, ext


# ---------------------------------------------------------------------------
# Extract object key from URL
# ---------------------------------------------------------------------------
def extract_object_key_from_url(url: str) -> Optional[str]:
    """
    Extract the S3 object key from a full public URL.

    Handles both S3_PUBLIC_URL-based and legacy ``/storage/`` URLs.
    Returns None if the key cannot be determined.
    """
    if not url:
        return None

    # Try S3_PUBLIC_URL prefix first
    if settings.S3_PUBLIC_URL:
        base = settings.S3_PUBLIC_URL.rstrip("/") + "/"
        if url.startswith(base):
            return url[len(base) :]

    # Fallback: endpoint-based URL
    endpoint_base = f"{settings.S3_ENDPOINT_URL.rstrip('/')}/{settings.S3_BUCKET_NAME}/"
    if url.startswith(endpoint_base):
        return url[len(endpoint_base) :]

    # Legacy /storage/ paths  (e.g. "/storage/images/abc.jpg")
    if "/storage/" in url:
        # Strip everything before /storage/ and the leading slash
        after = url.split("/storage/")[-1]
        # Map old paths: "images/x.jpg" -> "images/x.jpg",  "blogs/x.jpg" -> "blogs/x.jpg"
        return after

    # If it looks like just a bare filename, assume images/ prefix (backwards compat)
    if "/" not in url and "." in url:
        return f"images/{url}"

    return None


# ---------------------------------------------------------------------------
# Image upload
# ---------------------------------------------------------------------------
def _validate_image_bytes(data: bytes) -> None:
    """Validate that bytes represent a real image using PIL."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
    except Exception:
        raise HTTPException(
            status_code=400, detail="Invalid image file or corrupted image"
        )


async def upload_image(file: UploadFile, prefix: str = "images") -> str:
    """
    Upload a single image to S3.

    Args:
        file: FastAPI UploadFile.
        prefix: S3 key prefix (default ``images``).

    Returns:
        The unique filename (object key) stored in S3, e.g. ``images/uuid.jpg``.
    """
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type not allowed. Allowed: {', '.join(ALLOWED_IMAGE_EXTENSIONS)}",
        )

    contents = await file.read()

    if len(contents) > MAX_IMAGE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {MAX_IMAGE_SIZE // (1024 * 1024)}MB",
        )

    _validate_image_bytes(contents)

    object_key, _ = _generate_object_key(prefix, file.filename)
    content_type = CONTENT_TYPE_MAP.get(ext, "application/octet-stream")

    _upload_bytes_to_s3(contents, object_key, content_type)
    return object_key


async def upload_multiple_images(
    files: list[UploadFile], prefix: str = "images"
) -> list[str]:
    """
    Upload multiple images to S3.

    Returns:
        List of object keys.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    uploaded_keys: list[str] = []
    try:
        for f in files:
            key = await upload_image(f, prefix=prefix)
            uploaded_keys.append(key)
        return uploaded_keys
    except Exception:
        # Rollback: delete any already-uploaded files
        for key in uploaded_keys:
            delete_from_s3(key)
        raise


# ---------------------------------------------------------------------------
# Video upload
# ---------------------------------------------------------------------------
async def upload_video(file: UploadFile, prefix: str = "videos") -> str:
    """
    Upload a single video to S3.

    Returns:
        The object key, e.g. ``videos/uuid.mp4``.
    """
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type not allowed. Allowed: {', '.join(ALLOWED_VIDEO_EXTENSIONS)}",
        )

    contents = await file.read()

    if len(contents) > MAX_VIDEO_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {MAX_VIDEO_SIZE // (1024 * 1024)}MB",
        )

    object_key, _ = _generate_object_key(prefix, file.filename)
    content_type = CONTENT_TYPE_MAP.get(ext, "application/octet-stream")

    _upload_bytes_to_s3(contents, object_key, content_type)
    return object_key


# ---------------------------------------------------------------------------
# Delete image / video helper
# ---------------------------------------------------------------------------
def delete_file(object_key_or_url: str) -> bool:
    """
    Delete a file from S3. Accepts either an object key or a full URL.
    """
    key = extract_object_key_from_url(object_key_or_url)
    if key is None:
        key = object_key_or_url  # assume it's already a raw key
    return delete_from_s3(key)
