/* ===========================================================================
   Records Assist  --  release script 00: the app's own databases
   SERVER: the SQL Server instance that hosts the district's app databases.
           A script cannot check which server it is on. Read the title bar.
   DATABASE: master (pinned below)

   Run as a DBA, signed in as yourself. Safe to run twice.

   Creates two empty databases and nothing else:
     RecordsAssist       the hosted app's
     RecordsAssistTest   a scratch copy for tests/test_hosting.py; no app
                         identity is ever granted anything in it
   Run order:  00  ->  01-schema + 01-schema.test  ->  02-app-identity
   =========================================================================== */

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

USE [master];
GO

DECLARE @db sysname = DB_NAME();
IF @db <> N'master'
BEGIN
    RAISERROR(N'Wrong database: %s. Release script 00 must run in master.', 16, 1, @db);
    SET NOEXEC ON;
END
GO

IF DB_ID(N'RecordsAssist') IS NULL
BEGIN
    CREATE DATABASE [RecordsAssist];
    PRINT 'Created database RecordsAssist';
END
ELSE PRINT 'Database RecordsAssist already exists';
GO

IF DB_ID(N'RecordsAssistTest') IS NULL
BEGIN
    CREATE DATABASE [RecordsAssistTest];
    PRINT 'Created database RecordsAssistTest';
END
ELSE PRINT 'Database RecordsAssistTest already exists';
GO

SET NOEXEC OFF;
GO
