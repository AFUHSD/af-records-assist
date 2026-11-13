/* ===========================================================================
   Records Assist  --  release script 02: the app's identity and what it may do
   SERVER: the SQL Server instance that hosts the district's app databases.
           A script cannot check which server it is on. Read the title bar.
   DATABASE: master for the login, then RecordsAssistTest (both pinned below)

   Run as a DBA, after 00 and 01-schema, and after the app's Windows service
   account exists in the directory. Safe to run twice.
   02-app-identity.test.sql is this file with the database name changed: the
   same identity and the same rights in the test database, so the storage
   tests can run as the identity the app runs as.

   BEFORE RUNNING: replace the placeholder in @AppPrincipal below with the
   app's service account (DOMAIN\account). The release runner does this from
   its --principal argument.

   No password anywhere: the app signs in to SQL Server as its own Windows
   identity. What it gets, object by object, and nothing wider:
     dbo.Requests       SELECT, INSERT; UPDATE on Doc and UpdatedAt only   (no DELETE: nothing in the app deletes a request)
     dbo.AuditEvents    SELECT, INSERT             (append-only; UPDATE and DELETE are also denied)
     dbo.Access         SELECT                     (the access list is operator state; the app only reads it)
     dbo.RequestNumber  UPDATE                     (what NEXT VALUE FOR needs)
   It is in no database role and has no DDL rights, so it cannot alter its own
   schema or drop the audit trigger.
   =========================================================================== */

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

USE [master];
GO

DECLARE @AppPrincipal sysname = N'<DOMAIN\records-app-identity>';
DECLARE @db sysname = DB_NAME();
IF @db <> N'master'
BEGIN
    RAISERROR(N'Wrong database: %s. The first part of release script 02 must run in master.', 16, 1, @db);
    SET NOEXEC ON;
END
IF @AppPrincipal LIKE N'%<%' OR @AppPrincipal LIKE N'%>%' OR @AppPrincipal NOT LIKE N'%\%'
BEGIN
    RAISERROR(N'Set @AppPrincipal to the app service account (DOMAIN\account) before running release script 02.', 16, 1);
    SET NOEXEC ON;
END
IF DB_ID(N'RecordsAssistTest') IS NULL OR OBJECT_ID(N'RecordsAssistTest.dbo.AuditEvents', N'U') IS NULL
BEGIN
    RAISERROR(N'RecordsAssistTest or its schema is missing. Run release scripts 00 and 01-schema first.', 16, 1);
    SET NOEXEC ON;
END
GO

DECLARE @AppPrincipal sysname = N'<DOMAIN\records-app-identity>';
EXEC sys.sp_set_session_context @key = N'records_app_principal', @value = @AppPrincipal;
IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = @AppPrincipal)
BEGIN
    DECLARE @sql nvarchar(400) = N'CREATE LOGIN ' + QUOTENAME(@AppPrincipal) + N' FROM WINDOWS;';
    EXEC sys.sp_executesql @sql;
    PRINT 'Created login ' + @AppPrincipal;
END
ELSE PRINT 'Login ' + @AppPrincipal + ' already exists';
GO

USE [RecordsAssistTest];
GO

DECLARE @db sysname = DB_NAME();
IF @db <> N'RecordsAssistTest'
BEGIN
    RAISERROR(N'Wrong database: %s. The second part of release script 02 must run in RecordsAssistTest.', 16, 1, @db);
    SET NOEXEC ON;
END
GO

DECLARE @AppPrincipal sysname = CAST(SESSION_CONTEXT(N'records_app_principal') AS sysname);
IF @AppPrincipal IS NULL
BEGIN
    RAISERROR(N'The app identity was not carried over from the first part. Run the whole script on one connection.', 16, 1);
    SET NOEXEC ON;
END
GO

DECLARE @AppPrincipal sysname = CAST(SESSION_CONTEXT(N'records_app_principal') AS sysname);
DECLARE @who nvarchar(300) = QUOTENAME(@AppPrincipal);
DECLARE @sql nvarchar(max);

IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = @AppPrincipal)
BEGIN
    SET @sql = N'CREATE USER ' + @who + N' FOR LOGIN ' + @who + N';';
    EXEC sys.sp_executesql @sql;
    PRINT 'Created user ' + @AppPrincipal + ' in RecordsAssistTest';
END

SET @sql = N'
REVOKE UPDATE                ON dbo.Requests      FROM ' + @who + N';
GRANT SELECT, INSERT         ON dbo.Requests      TO ' + @who + N';
GRANT UPDATE (Doc, UpdatedAt) ON dbo.Requests     TO ' + @who + N';
GRANT SELECT, INSERT         ON dbo.AuditEvents   TO ' + @who + N';
DENY  UPDATE, DELETE         ON dbo.AuditEvents   TO ' + @who + N';
GRANT SELECT                 ON dbo.Access        TO ' + @who + N';
DENY  INSERT, UPDATE, DELETE ON dbo.Access        TO ' + @who + N';
GRANT UPDATE                 ON dbo.RequestNumber TO ' + @who + N';';
EXEC sys.sp_executesql @sql;
PRINT 'Granted the object rights listed in the header to ' + @AppPrincipal;
GO

/* Read back what the identity now holds, so the run shows it rather than asserting it. */
DECLARE @AppPrincipal sysname = CAST(SESSION_CONTEXT(N'records_app_principal') AS sysname);
SELECT o.name AS [object], p.state_desc AS [state], p.permission_name AS [permission]
FROM sys.database_permissions AS p
JOIN sys.database_principals AS u ON u.principal_id = p.grantee_principal_id
LEFT JOIN sys.objects AS o ON o.object_id = p.major_id AND p.class = 1
WHERE u.name = @AppPrincipal
ORDER BY o.name, p.permission_name;
SELECT r.name AS [role the identity is in (expect no rows)]
FROM sys.database_role_members AS m
JOIN sys.database_principals AS r ON r.principal_id = m.role_principal_id
JOIN sys.database_principals AS u ON u.principal_id = m.member_principal_id
WHERE u.name = @AppPrincipal;
GO

/* Loud, not just listed: an identity that is already in a role, or a server
   sysadmin, holds more than this script grants, and the app must not run as it. */
DECLARE @AppPrincipal sysname = CAST(SESSION_CONTEXT(N'records_app_principal') AS sysname);
IF EXISTS (SELECT 1 FROM sys.database_role_members AS m JOIN sys.database_principals AS u ON u.principal_id = m.member_principal_id WHERE u.name = @AppPrincipal)
   OR IS_SRVROLEMEMBER(N'sysadmin', @AppPrincipal) = 1
    RAISERROR(N'%s holds a database role or is sysadmin. Remove that before the app runs as it.', 16, 1, @AppPrincipal);
ELSE PRINT 'Checked: ' + @AppPrincipal + ' is in no database role and is not sysadmin';

/* The database user must be this login and not an older user of the same name. */
IF NOT EXISTS (SELECT 1 FROM sys.database_principals AS u JOIN sys.server_principals AS l ON l.sid = u.sid
               WHERE u.name = @AppPrincipal AND l.name = @AppPrincipal)
    RAISERROR(N'The database user %s is not mapped to the login of that name. Drop the user and run this script again.', 16, 1, @AppPrincipal);
ELSE PRINT 'Checked: the database user is mapped to the login';

/* Nothing beyond the list in the header: any other grant held directly in this
   database, or on the server beyond connecting, fails the run. */
IF EXISTS (SELECT 1
           FROM sys.database_permissions AS p
           JOIN sys.database_principals AS u ON u.principal_id = p.grantee_principal_id
           LEFT JOIN sys.objects AS o ON o.object_id = p.major_id AND p.class = 1 AND o.schema_id = SCHEMA_ID(N'dbo')
           WHERE u.name = @AppPrincipal AND p.state IN ('G', 'W')
             AND NOT (   (p.class = 0 AND p.permission_name = N'CONNECT')
                      OR (o.name = N'Requests' AND p.minor_id = 0 AND p.permission_name IN (N'SELECT', N'INSERT'))
                      OR (o.name = N'Requests' AND p.minor_id <> 0 AND p.permission_name = N'UPDATE'
                          AND COL_NAME(p.major_id, p.minor_id) IN (N'Doc', N'UpdatedAt'))
                      OR (o.name = N'AuditEvents' AND p.minor_id = 0 AND p.permission_name IN (N'SELECT', N'INSERT'))
                      OR (o.name = N'Access' AND p.minor_id = 0 AND p.permission_name = N'SELECT')
                      OR (o.name = N'RequestNumber' AND p.permission_name = N'UPDATE')))
    RAISERROR(N'%s holds a grant in this database beyond the list in the header (see the read-back above). Revoke it before the app runs as it.', 16, 1, @AppPrincipal);
ELSE PRINT 'Checked: no grant in this database beyond the list in the header';

IF EXISTS (SELECT 1 FROM sys.server_permissions AS p JOIN sys.server_principals AS l ON l.principal_id = p.grantee_principal_id
           WHERE l.name = @AppPrincipal AND p.state IN ('G', 'W') AND p.permission_name <> N'CONNECT SQL')
   OR EXISTS (SELECT 1 FROM sys.server_role_members AS m JOIN sys.server_principals AS l ON l.principal_id = m.member_principal_id WHERE l.name = @AppPrincipal)
    RAISERROR(N'%s holds a server permission or server role beyond connecting. Remove it before the app runs as it.', 16, 1, @AppPrincipal);
ELSE PRINT 'Checked: no server permission or server role beyond connecting';
/* Not checked here: rights the account inherits from a Windows group that has
   its own login. The account is meant to be in no groups; confirm that in AD. */
GO

SET NOEXEC OFF;
GO
