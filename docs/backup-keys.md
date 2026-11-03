# Backup age key custody

This guide covers age encryption keys for the operator-run offsite backups. It contains no key material. Never commit a generated identity, recipient value, environment file, database URL, S3 credential, or alert hook.

## Generate the primary and recovery keys

Use a trusted operator workstation with age installed. Generate two independent identities; do not copy the primary private identity to the recovery custodian or vice versa.

```sh
umask 077
age-keygen -o /secure/backup-keys/ajo-primary.txt
age-keygen -o /secure/backup-keys/ajo-recovery.txt
chmod 600 /secure/backup-keys/ajo-primary.txt /secure/backup-keys/ajo-recovery.txt
age-keygen -y /secure/backup-keys/ajo-primary.txt
age-keygen -y /secure/backup-keys/ajo-recovery.txt
```

Capture only the public recipient outputs in the operator's protected environment as the comma-separated `BACKUP_AGE_RECIPIENTS` value. It must contain both the primary and recovery public recipients. The backup script refuses to run with fewer than two valid recipients. Public recipients are not private keys, but keep them out of source control and logs too.

## Custody and locations

- **Primary identity:** keep the private identity in encrypted offline storage controlled by the designated primary backup operator. Do not leave it on the backup host, in a cloud bucket, in a repository, in GitHub Actions, or in a general-purpose synced folder. Bring or mount it only for an authorized restore verification or recovery.
- **Recovery identity:** keep its private identity in a separately encrypted offline location under a separate recovery custodian. Use a separate account/device and, where practical, a separate physical location from the primary copy. Do not make the primary and recovery files two copies under one person's everyday account.
- **Operational environment:** the backup host may hold the database URL, Supabase S3 endpoint/region/access credentials, public age recipients, and alert-hook environment values in an operator-protected environment file. It must not hold either private age identity. The verifier receives an identity **file path** as its sole argument and reads that file in place; it does not copy or persist the private key.
- **Encrypted objects:** the private Supabase bucket contains only encrypted `.age` backup artifacts and their checksum companions. The laptop pull script downloads them additively and does not delete local files.

Keep encrypted offline key media access-controlled. Record custodians, storage locations, and access/recovery procedure in an approved private operations record, not in this document or the repository. If a key is lost, do not claim that the corresponding backups are recoverable.

## Monthly decrypt and restore test

At least monthly, an authorized operator should perform a full verification with **each** identity: one run with the primary identity and a separate run with the recovery identity under the recovery custodian. This confirms both recipients can decrypt rather than merely proving a backup object exists.

1. On the operator machine, load the environment-only S3 and alert configuration from its protected local source. Do not place the private identity in that environment file.
2. Attach or stage the selected private identity only from its protected offline store. Restrict access to the operator; use a protected, non-synced path. The script itself never copies the identity into its temporary restore directory.
3. Run the verifier against the local disposable PostgreSQL 18 container:

   ```sh
   ops/backup/verify_restore.sh /secure/offline-mounted/age-identity.txt
   ```

4. Confirm the verifier reports success, checksum and manifest checks passed, the ACL and schema validator completed, and the container and temporary decrypted files were removed. Record only the date, which custodian/key label was used (for example, “primary” or “recovery”), duration, pass/fail, and remediation in the private operations record. Never record a key, recipient value, URL, credential, member data, or dump contents.
5. Detach the offline medium or remove any operator-created temporary staging copy according to its secure-media procedure. Do not leave the identity on the backup server or laptop.

A failed check must page the operators through the configured hooks, be investigated, and be repeated after remediation. A checksum check without a successful age decrypt and restore is not a decrypt test.

## Key rotation

1. Generate a new independent primary and recovery pair and validate their custodians and offline storage before changing the live recipient list.
2. Add both new public recipients to `BACKUP_AGE_RECIPIENTS` in the protected backup environment, retaining the old recipients during transition. New backups will then be decryptable by both new and old identities.
3. Create a fresh backup and perform a decrypt/restore test with each new identity. Confirm at least one good post-rotation backup is available before removing any recipient.
4. Retain the old private identities in their protected offline locations for as long as any retained backup might have been encrypted only to those recipients. With the default six monthly retention windows plus weekly/daily overlap, keep the old identities for at least seven months after the final old-recipient-only backup, and longer if retention is configured beyond that.
5. After the old-only backups have expired and the new primary and recovery tests pass, remove the old public recipients from the environment, retire the old identities using the secure-media procedure, and record the rotation date and outcome without recording key material.

If a private identity is suspected compromised, treat it as an incident: immediately add new recipients, verify a fresh backup with new identities, restrict affected access, and follow the incident-response runbook. Rotation cannot revoke access to already-downloaded ciphertext encrypted to the compromised recipient; preserve the incident evidence and assess the retention window with the owner.
