"""Where product photos are kept.

Local folder for development. In production this will be object storage (S3-compatible, e.g.
Cloudflare R2) so photos are served from a CDN instead of using the web server's bandwidth.
Set PHOTO_STORAGE=s3 plus the S3_* variables once that's set up.
"""
import os
import pathlib


class LocalStorage:
    def __init__(self, root):
        self.root = pathlib.Path(root)

    def exists(self, key):
        return (self.root / key).exists()

    def get(self, key):
        return (self.root / key).read_bytes()

    def put(self, key, data, content_type="image/jpeg"):
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def url(self, key):
        return "/photos/" + key


class S3Storage:
    """S3-compatible object storage (AWS S3, Cloudflare R2, Backblaze B2...)."""

    def __init__(self):
        import boto3  # only needed in production
        self.bucket = os.environ["S3_BUCKET"]
        self.public_base = os.environ.get("S3_PUBLIC_URL", "").rstrip("/")
        self.client = boto3.client("s3", endpoint_url=os.environ.get("S3_ENDPOINT"),
                                   aws_access_key_id=os.environ["S3_ACCESS_KEY_ID"],
                                   aws_secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"])

    def exists(self, key):
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def get(self, key):
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def put(self, key, data, content_type="image/jpeg"):
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type,
                               CacheControl="public, max-age=31536000, immutable")

    def url(self, key):
        return f"{self.public_base}/{key}"


def get_storage():
    if os.environ.get("PHOTO_STORAGE", "local") == "s3":
        return S3Storage()
    return LocalStorage(os.environ.get("PHOTO_DIR", "data/photos"))
