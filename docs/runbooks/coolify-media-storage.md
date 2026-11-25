# Shared media storage on Coolify

Mera's API writes uploads and the publishing worker reads them. When those services are
separate Coolify resources, each resource's automatically-created named volume is private,
even if both mounts use `/var/lib/mera/media` inside the container. Use one Coolify host
directory for both services.

## Configure the shared directory

On the Docker host, create a persistent directory owned by the non-root Mera container user:

```sh
sudo install -d -o 10001 -g 999 -m 0750 /data/coolify/mera/media
```

Set the same environment variable on the API and worker resources:

```dotenv
MERA_MEDIA_HOST_PATH=/data/coolify/mera/media
MEDIA_ROOT=/var/lib/mera/media
```

`docker-compose.api.yml` and `docker-compose.worker.yml` bind that host directory to
`/var/lib/mera/media`. Keep the API and worker on the same Coolify server. If Coolify's UI
manages storage instead of these Compose files, add a **Directory Mount** with the same
source and destination to both resources.

## Preserve existing uploads

Do not switch to an empty directory before copying the API's current media volume. Locate
the API's existing volume and copy its contents while publishing is paused:

```sh
docker inspect <api-container> --format '{{json .Mounts}}'
sudo cp -a <existing-media-source>/. /data/coolify/mera/media/
sudo chown -R 10001:999 /data/coolify/mera/media
```

Then redeploy API and worker and verify the same object from both containers:

```sh
docker exec <api-container> test -r /var/lib/mera/media/ws/<workspace>/<asset>.jpg
docker exec <worker-container> test -r /var/lib/mera/media/ws/<workspace>/<asset>.jpg
```

The worker now classifies a missing attachment as `MEDIA_NOT_FOUND`, moves the scheduled
post to `dead_lettered`, and stops automatic dispatch. After restoring or replacing the
attachment, use the existing manual Retry action; all approval and idempotency checks run
again before publication.
