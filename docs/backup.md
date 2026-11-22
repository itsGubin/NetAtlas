# Backup, restore and upgrade

A NetAtlas install holds three things worth keeping:

- **The database:** every item, Page, user, API token and the whole History.
- **The attachments:** the files on the `media` volume.
- **`.env`:** the secret key and database password.

Run every command from the folder that holds `compose.yaml`.

**Backups are sensitive.** They contain `.env`, password hashes and your full network documentation. The commands below keep them in a `backups` folder only your user can read, which Git and Docker builds ignore. Encrypt them before copying them anywhere else.

## Back up

This stops the app for a moment, so the database and the attachments match each other.

<!-- ci:backup -->
```sh
stamp=$(date +%Y%m%d-%H%M%S)
mkdir -p -m 700 backups
cp .env "backups/env-$stamp"
if docker compose stop app &&
  docker compose exec -T db pg_dump -U netatlas -d netatlas --format=custom > "backups/netatlas-$stamp.dump" &&
  docker compose run --rm --no-deps -T --entrypoint tar app -C /data/media -czf - . > "backups/media-$stamp.tar.gz" &&
  docker compose exec -T db pg_restore --list < "backups/netatlas-$stamp.dump" > /dev/null &&
  tar -tzf "backups/media-$stamp.tar.gz" > /dev/null
then
  chmod 600 "backups/env-$stamp" "backups/netatlas-$stamp.dump" "backups/media-$stamp.tar.gz"
  echo "Backup complete: $stamp"
else
  echo "BACKUP FAILED" >&2
  rm -f "backups/env-$stamp" "backups/netatlas-$stamp.dump" "backups/media-$stamp.tar.gz"
fi
docker compose start app
```

Check that it prints `Backup complete`. The last two checks read the finished files back, so a damaged backup is reported now and not on the day you need it.

Keep copies on another machine. A backup on the same disk does not survive that disk failing.

## Restore

Restore into the **same NetAtlas version** that made the backup, on a fresh install: clone the repository, check out that version and copy the `backups` folder into it.

To replace an existing install instead, first delete its data. **This permanently deletes the current database and attachments**, so make sure your backup is good:

```sh
docker compose down -v
```

Then set `stamp` to the backup you want and restore it:

<!-- ci:restore -->
```sh
stamp=20261122-120000
cp "backups/env-$stamp" .env
docker compose build app
docker compose up -d --wait db
docker compose exec -T db pg_restore -U netatlas -d netatlas --clean --if-exists --no-owner --exit-on-error --single-transaction < "backups/netatlas-$stamp.dump"
docker compose run --rm --no-deps -T --entrypoint tar app -C /data/media -xzf - < "backups/media-$stamp.tar.gz"
docker compose up -d --wait
```

Log in and open a Page with an attachment to confirm it worked. Users, Roles and API tokens are restored as they were.

## Upgrade

1. **Back up first**, using the steps above.
2. Get the new version and restart. NetAtlas is built from source, so there is no image to pull. Database migrations run automatically on start.

<!-- ci:upgrade -->
```sh
git pull
docker compose up -d --build --wait
```

3. Log in and check that everything looks right.

**If an upgrade goes wrong**, go back to the version you had, delete the data with `docker compose down -v` and restore your backup. Don't start the old version on a database that the new version has already migrated.

**Upgrading Postgres itself** to a new major version also works through a backup: change the `postgres` image in `compose.yaml`, delete the data with `docker compose down -v` and restore.
