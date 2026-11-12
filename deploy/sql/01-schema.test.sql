/* ===========================================================================
   Records Assist  --  schema for the app's own database
   SERVER: the SQL Server instance that hosts the district's app databases.
           A script cannot check which server it is on. Read the title bar.
   DATABASE: RecordsAssistTest  (pinned below; change BOTH lines to rehearse on a
           copy such as RecordsAssistTest)

   Run as a DBA, after the database and the app's login exist. Safe to run
   twice: every object is created only if it is missing.

   What lives here: request documents (who asked, the search plan, the status
   of each record, every redaction decision by position and category, the cost
   ledger), the audit trail, and the access list.
   What never lives here: the words of any record. Record text, subjects and
   the models' reasons stay in the app's protected data folder (web/store.py).

   The app's identity gets read, write and execute on this database and
   nothing more: no DDL, and it cannot change or delete an audit line.
   =========================================================================== */

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

USE [RecordsAssistTest];
GO

DECLARE @db sysname = DB_NAME();
IF @db <> N'RecordsAssistTest'
BEGIN
    RAISERROR(N'Wrong database: %s. This script must run in RecordsAssistTest.', 16, 1, @db);
    SET NOEXEC ON;
END
GO

/* One transaction for the whole schema. The runner stops at the first failing
   batch and closes the connection, which rolls everything back: the database is
   either as it was or complete, never half-built. */
BEGIN TRANSACTION;
GO

IF NOT EXISTS (SELECT 1 FROM sys.sequences WHERE name = N'RequestNumber' AND schema_id = SCHEMA_ID(N'dbo'))
BEGIN
    CREATE SEQUENCE dbo.RequestNumber AS int START WITH 1 INCREMENT BY 1 NO CYCLE NO CACHE;
    PRINT 'Added sequence dbo.RequestNumber';
END
GO

IF OBJECT_ID(N'dbo.Requests', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.Requests
    (
        RequestId  varchar(12)   NOT NULL CONSTRAINT PK_Requests PRIMARY KEY,
        Doc        nvarchar(max) NOT NULL CONSTRAINT CK_Requests_Doc_IsJson CHECK (ISJSON(Doc) = 1),
        CreatedAt  datetime2(0)  NOT NULL CONSTRAINT DF_Requests_CreatedAt DEFAULT (SYSUTCDATETIME()),
        UpdatedAt  datetime2(0)  NOT NULL CONSTRAINT DF_Requests_UpdatedAt DEFAULT (SYSUTCDATETIME()),
        RowVer     rowversion    NOT NULL,
        CONSTRAINT CK_Requests_RequestId CHECK (RequestId LIKE 'REQ-[0-9][0-9][0-9][0-9]%' AND SUBSTRING(RequestId, 5, 8) NOT LIKE '%[^0-9]%')
    );
    PRINT 'Added table dbo.Requests';
END
GO

IF OBJECT_ID(N'dbo.AuditEvents', N'U') IS NULL
BEGIN
    /* Who did what, when. Times are UTC. Detail is JSON: record ids, positions,
       categories, file hashes. Never record text. */
    CREATE TABLE dbo.AuditEvents
    (
        EventId    bigint IDENTITY(1,1) NOT NULL CONSTRAINT PK_AuditEvents PRIMARY KEY,
        RequestId  varchar(12)    NULL,
        At         datetime2(3)   NOT NULL CONSTRAINT DF_AuditEvents_At DEFAULT (SYSUTCDATETIME()),
        Actor      nvarchar(256)  NOT NULL,
        Action     varchar(40)    NOT NULL,
        Detail     nvarchar(max)  NULL CONSTRAINT CK_AuditEvents_Detail_IsJson CHECK (Detail IS NULL OR ISJSON(Detail) = 1)
    );
    PRINT 'Added table dbo.AuditEvents';
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_AuditEvents_Request' AND object_id = OBJECT_ID(N'dbo.AuditEvents'))
BEGIN
    CREATE INDEX IX_AuditEvents_Request ON dbo.AuditEvents (RequestId, EventId);
    PRINT 'Added index IX_AuditEvents_Request';
END
GO

IF OBJECT_ID(N'dbo.Access', N'U') IS NULL
BEGIN
    /* The access list: who may open the app at all. Operator state. A deploy
       never writes here; a DBA or an admin adds and removes rows. */
    CREATE TABLE dbo.Access
    (
        Email    nvarchar(256) NOT NULL CONSTRAINT PK_Access PRIMARY KEY,
        Role     varchar(20)   NOT NULL CONSTRAINT CK_Access_Role CHECK (Role IN ('reviewer', 'admin')),
        AddedBy  nvarchar(256) NOT NULL CONSTRAINT DF_Access_AddedBy DEFAULT (ORIGINAL_LOGIN()),
        AddedAt  datetime2(0)  NOT NULL CONSTRAINT DF_Access_AddedAt DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT CK_Access_Email_Lower CHECK (Email = LOWER(Email) COLLATE Latin1_General_BIN2)
    );
    PRINT 'Added table dbo.Access';
END
GO

/* The audit trail is append-only for everybody, not only for the app: a
   trigger refuses UPDATE and DELETE whoever asks. Dropping the trigger is
   itself DDL, which the app's identity cannot do. */
IF OBJECT_ID(N'dbo.TR_AuditEvents_AppendOnly', N'TR') IS NULL
    EXEC (N'
CREATE TRIGGER dbo.TR_AuditEvents_AppendOnly ON dbo.AuditEvents
INSTEAD OF UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    IF EXISTS (SELECT 1 FROM deleted)
        THROW 51000, N''dbo.AuditEvents is append-only: rows cannot be changed or deleted.'', 1;
END');
GO
/* Prove what is there before committing, rather than trusting that each IF did
   what it said: every object, and the trigger enabled. Anything missing fails
   the batch, and the transaction never commits. */
DECLARE @missing nvarchar(400) = N'';
IF NOT EXISTS (SELECT 1 FROM sys.sequences WHERE name = N'RequestNumber') SET @missing += N' sequence RequestNumber;';
IF COL_LENGTH(N'dbo.Requests', N'RowVer') IS NULL OR COL_LENGTH(N'dbo.Requests', N'Doc') IS NULL SET @missing += N' table Requests (Doc, RowVer);';
IF COL_LENGTH(N'dbo.AuditEvents', N'Detail') IS NULL OR COL_LENGTH(N'dbo.AuditEvents', N'Actor') IS NULL SET @missing += N' table AuditEvents;';
IF COL_LENGTH(N'dbo.Access', N'Role') IS NULL SET @missing += N' table Access;';
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_AuditEvents_Request') SET @missing += N' index IX_AuditEvents_Request;';
IF NOT EXISTS (SELECT 1 FROM sys.triggers WHERE name = N'TR_AuditEvents_AppendOnly' AND is_disabled = 0 AND is_instead_of_trigger = 1)
    SET @missing += N' trigger TR_AuditEvents_AppendOnly (enabled);';
IF @missing <> N''
BEGIN
    RAISERROR(N'Schema is incomplete, nothing was committed. Missing:%s', 16, 1, @missing);
    ROLLBACK TRANSACTION;
    SET NOEXEC ON;
END
GO

COMMIT TRANSACTION;
PRINT 'Schema is complete and committed: sequence, 3 tables, index, append-only trigger';
GO

SET NOEXEC OFF;
GO
