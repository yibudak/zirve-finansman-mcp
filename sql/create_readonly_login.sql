-- zirve-finansman-mcp için salt-okunur SQL kullanıcısı.
-- Bir SQL yöneticisi (sysadmin) tarafından çalıştırılmalıdır. Parolayı değiştirin.
-- Sunucuda "SQL Server and Windows Authentication" (karma mod) açık olmalıdır; Zirve kurulumlarında açıktır.

USE master;
CREATE LOGIN zirve_mcp_ro WITH PASSWORD = N'BURAYA-GUCLU-BIR-PAROLA', CHECK_POLICY = ON;
GO

-- SEÇENEK A (önerilen, dar): yalnızca MCP'nin okuduğu üç tabloya, yalnızca seçtiğiniz firmada yetki.
-- Her yeni yıl veritabanı açıldığında yeniden çalıştırılmalıdır. @firma değerini değiştirin.
DECLARE @firma sysname = N'FIRMA_ADI', @db sysname, @sql nvarchar(max);
DECLARE c CURSOR LOCAL FAST_FORWARD FOR
    SELECT name FROM sys.databases
    WHERE state = 0 AND name LIKE REPLACE(@firma, '_', '[_]') + '[_][0-9][0-9][0-9][0-9]';
OPEN c;
FETCH NEXT FROM c INTO @db;
WHILE @@FETCH_STATUS = 0
BEGIN
    SET @sql = N'USE ' + QUOTENAME(@db) + N';
        IF USER_ID(''zirve_mcp_ro'') IS NULL CREATE USER zirve_mcp_ro FOR LOGIN zirve_mcp_ro;
        GRANT SELECT ON dbo.hesplan TO zirve_mcp_ro;
        GRANT SELECT ON dbo.fisbil  TO zirve_mcp_ro;
        GRANT SELECT ON dbo.yevmiye TO zirve_mcp_ro;';
    EXEC (@sql);
    FETCH NEXT FROM c INTO @db;
END
CLOSE c; DEALLOCATE c;
GO

-- SEÇENEK B (kolay, geniş): tüm veritabanlarında, ileride açılacaklar dahil, her tabloyu okuma yetkisi.
-- DİKKAT: bordro/personel verisinin durduğu ..._GENEL veritabanlarını da okunabilir kılar.
-- SQL Server 2014 ve üzeri gerekir. A yerine kullanacaksanız aşağıdaki iki satırı açın.
-- GRANT CONNECT ANY DATABASE TO zirve_mcp_ro;
-- GRANT SELECT ALL USER SECURABLES TO zirve_mcp_ro;
