import asyncio
from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from rag.core.config import get_settings


class ObjectStorage:
    def __init__(self) -> None:
        s = get_settings()
        self._bucket = s.s3_bucket
        self._s3: Any = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint_url,
            aws_access_key_id=s.s3_access_key.get_secret_value(),
            aws_secret_access_key=s.s3_secret_key.get_secret_value(),
            region_name=s.s3_region,
            config=Config(connect_timeout=5, read_timeout=30, retries={"max_attempts": 3}),
        )

    async def ensure_bucket(self) -> None:
        def _ensure() -> None:
            try:
                self._s3.head_bucket(Bucket=self._bucket)
            except ClientError:
                self._s3.create_bucket(Bucket=self._bucket)

        await asyncio.to_thread(_ensure)

    async def healthy(self) -> bool:
        try:
            await asyncio.to_thread(self._s3.head_bucket, Bucket=self._bucket)
            return True
        except Exception:
            return False

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(self._s3.put_object, Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)

    async def get(self, key: str) -> bytes:
        resp = await asyncio.to_thread(self._s3.get_object, Bucket=self._bucket, Key=key)

        def read_body() -> bytes:
            with resp["Body"] as body:
                data: bytes = body.read()
                return data

        return await asyncio.to_thread(read_body)

    async def delete_prefix(self, prefix: str) -> None:
        def _delete() -> None:
            paginator = self._s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
                if keys:
                    result = self._s3.delete_objects(Bucket=self._bucket, Delete={"Objects": keys})
                    if result.get("Errors"):
                        raise RuntimeError("Object storage could not delete all document files")

        await asyncio.to_thread(_delete)


@lru_cache
def get_storage() -> ObjectStorage:
    return ObjectStorage()
