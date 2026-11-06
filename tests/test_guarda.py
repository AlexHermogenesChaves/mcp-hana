import unittest

from hana_diag_mcp.guarda import ConsultaBloqueada, validar_somente_leitura


class PermiteLeitura(unittest.TestCase):
    def test_select_simples(self):
        self.assertEqual(validar_somente_leitura("SELECT 1 FROM DUMMY"), "SELECT 1 FROM DUMMY")

    def test_with_cte(self):
        validar_somente_leitura('WITH X AS (SELECT "ItemCode" FROM OITM) SELECT * FROM X')

    def test_ponto_e_virgula_final_removido(self):
        self.assertEqual(validar_somente_leitura("SELECT 1 FROM DUMMY;  "), "SELECT 1 FROM DUMMY")

    def test_ponto_e_virgula_final_com_comentario_depois(self):
        validar_somente_leitura("SELECT 1 FROM DUMMY; -- fim")

    def test_palavra_proibida_dentro_de_literal(self):
        validar_somente_leitura("SELECT * FROM OITM WHERE \"ItemName\" = 'DELETE; DROP TABLE OITM'")

    def test_palavra_proibida_em_identificador_entre_aspas(self):
        validar_somente_leitura('SELECT "U_Update", "Delete" FROM "@MINHATAB"')

    def test_palavra_proibida_em_comentario(self):
        validar_somente_leitura("-- não vou dar UPDATE\nSELECT 1 FROM DUMMY /* nem INSERT */")

    def test_funcao_replace(self):
        validar_somente_leitura("SELECT REPLACE(\"ItemName\", 'a', 'b') FROM OITM")

    def test_aspas_escapadas(self):
        validar_somente_leitura("SELECT 'it''s; DELETE' FROM DUMMY")

    def test_table_function(self):
        validar_somente_leitura('SELECT * FROM "SCH"."TF_ESTOQUE"(P_INI => \'20260101\')')


class BloqueiaEscrita(unittest.TestCase):
    CASOS = [
        "UPDATE OITM SET \"ItemName\" = 'x'",
        "insert into OITM values (1)",
        "DELETE FROM OITM",
        "MERGE INTO OITM USING X ON 1=1 WHEN MATCHED THEN DELETE",
        "UPSERT OITM VALUES (1) WITH PRIMARY KEY",
        "REPLACE OITM VALUES (1) WITH PRIMARY KEY",
        "TRUNCATE TABLE OITM",
        "DROP TABLE OITM",
        "CREATE TABLE X (A INT)",
        "ALTER TABLE OITM ADD (A INT)",
        "CALL SBO_SP_TRANSACTIONNOTIFICATION(1,2,3,4,5)",
        "DO BEGIN DELETE FROM OITM; END",
        "GRANT SELECT ON OITM TO X",
        "SET SCHEMA X",
        "COMMIT",
        "SELECT 1 FROM DUMMY; DELETE FROM OITM",
        "SELECT 1 FROM DUMMY;DELETE FROM OITM;",
        "SELECT * INTO X FROM OITM",
        "SELECT * FROM OITM FOR UPDATE",
        "SELECT * FROM OITM WITH HINT(X) FOR SHARE LOCK",
        "WITH X AS (SELECT 1 A FROM DUMMY) INSERT INTO T SELECT * FROM X",
        "/* SELECT */ DELETE FROM OITM",
        "-- SELECT\nDELETE FROM OITM",
        "EXPLAIN PLAN FOR SELECT 1 FROM DUMMY",
        "  ",
        "SELECT 'sem fechar FROM DUMMY",
        "SELECT 1 /* sem fechar",
    ]

    def test_todos_bloqueados(self):
        for sql in self.CASOS:
            with self.subTest(sql=sql):
                with self.assertRaises(ConsultaBloqueada):
                    validar_somente_leitura(sql)


if __name__ == "__main__":
    unittest.main()
