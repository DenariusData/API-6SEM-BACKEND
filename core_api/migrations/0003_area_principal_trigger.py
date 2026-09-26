"""
Garante, no nível do banco (não só na aplicação), que "todo documento
tem exatamente uma área principal". A ORM sozinha (UniqueConstraint)
só impede DUAS principais; não garante a existência de UMA.
"""

from django.db import migrations

SQL_UP = """
CREATE OR REPLACE FUNCTION fn_check_documento_area_principal()
RETURNS TRIGGER AS $$
DECLARE
    v_doc_id UUID := COALESCE(NEW.documento_id, OLD.documento_id);
    v_count  INT;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM documento WHERE id = v_doc_id) THEN
        RETURN NULL;
    END IF;

    SELECT count(*) INTO v_count FROM documento_area
    WHERE documento_id = v_doc_id AND is_principal = TRUE;

    IF v_count <> 1 THEN
        RAISE EXCEPTION 'Documento % deve ter exatamente uma área principal (encontradas: %)', v_doc_id, v_count;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER trg_documento_area_principal
AFTER INSERT OR UPDATE OR DELETE ON documento_area
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION fn_check_documento_area_principal();

CREATE OR REPLACE FUNCTION fn_check_documento_tem_area_principal()
RETURNS TRIGGER AS $$
DECLARE
    v_count INT;
BEGIN
    SELECT count(*) INTO v_count FROM documento_area
    WHERE documento_id = NEW.id AND is_principal = TRUE;

    IF v_count <> 1 THEN
        RAISE EXCEPTION 'Documento % precisa ter exatamente uma área principal vinculada (R2P3/R2P4)', NEW.id;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER trg_documento_tem_area_principal
AFTER INSERT OR UPDATE ON documento
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION fn_check_documento_tem_area_principal();
"""

SQL_DOWN = """
DROP TRIGGER IF EXISTS trg_documento_tem_area_principal ON documento;
DROP FUNCTION IF EXISTS fn_check_documento_tem_area_principal();
DROP TRIGGER IF EXISTS trg_documento_area_principal ON documento_area;
DROP FUNCTION IF EXISTS fn_check_documento_area_principal();
"""


class Migration(migrations.Migration):
    dependencies = [("core_api", "0002_taxonomia_e_documento")]
    operations = [migrations.RunSQL(SQL_UP, reverse_sql=SQL_DOWN)]
