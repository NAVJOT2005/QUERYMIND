from pathlib import Path

from querymind.workspace import (
    find_existing_sql_queries,
    parse_django,
    parse_prisma,
    parse_sqlalchemy,
    propose_memory_addition,
    scan_workspace_models,
    search_workspace,
)


def test_parse_prisma(tmp_path: Path):
    prisma_content = """
    datasource db {
      provider = "mysql"
      url      = env("DATABASE_URL")
    }

    /// User account information
    model User {
      id        Int      @id @default(autoincrement())
      email     String   @unique
      /// Customer full legal name
      name      String?
      orders    Order[]
    }

    model Order {
      id        Int      @id @default(autoincrement())
      userId    Int
      user      User     @relation(fields: [userId], references: [id])
    }
    """
    prisma_file = tmp_path / "schema.prisma"
    prisma_file.write_text(prisma_content, encoding="utf-8")

    models = parse_prisma(prisma_file)
    assert len(models) == 2
    user_m = next(m for m in models if m.name == "User")
    assert user_m.docstring == "User account information"
    assert "email" in user_m.fields
    assert "name" in user_m.comments
    assert "legal name" in user_m.comments["name"]


def test_parse_sqlalchemy(tmp_path: Path):
    sa_content = '''
    from sqlalchemy import Column, Integer, String, ForeignKey
    from sqlalchemy.orm import declarative_base

    Base = declarative_base()

    class CustomerModel(Base):
        """Active platform customer record."""
        __tablename__ = 'customers'

        id = Column(Integer, primary_key=True)
        name = Column(String(100), comment="Full name")
        city = Column(String(50))
    '''
    py_file = tmp_path / "models.py"
    py_file.write_text(sa_content, encoding="utf-8")

    models = parse_sqlalchemy(py_file)
    assert len(models) == 1
    assert models[0].name == "customers"
    assert models[0].docstring == "Active platform customer record."
    assert models[0].comments.get("name") == "Full name"


def test_parse_django(tmp_path: Path):
    django_content = '''
    from django.db import models

    class Product(models.Model):
        """Sellable inventory item."""
        name = models.CharField(max_length=120, help_text="Public product title")
        price = models.DecimalField(max_digits=10, decimal_places=2)
    '''
    py_file = tmp_path / "models.py"
    py_file.write_text(django_content, encoding="utf-8")

    models = parse_django(py_file)
    assert len(models) == 1
    assert models[0].name == "Product"
    assert "Public product title" in models[0].comments.get("name", "")


def test_search_workspace_and_queries(tmp_path: Path):
    sql_file = tmp_path / "reports.sql"
    sql_file.write_text("-- Calculate customer churn rate\nSELECT customer_id FROM orders;\n", encoding="utf-8")

    doc_file = tmp_path / "BUSINESS_LOGIC.md"
    doc_file.write_text("# Definitions\nRevenue excludes refunds and cancelled orders.\n", encoding="utf-8")

    res = search_workspace(tmp_path, "churn")
    assert "churn" in res.lower()
    assert "reports.sql" in res

    found_sql = find_existing_sql_queries(tmp_path, "orders")
    assert len(found_sql) >= 1
    assert "orders" in found_sql[0][1]


def test_propose_memory_addition(tmp_path: Path):
    notes = tmp_path / "QUERYMIND.md"
    notes.write_text("# Project Notes\n\n## Definitions\n- revenue = net of discounts\n", encoding="utf-8")

    propose_memory_addition(tmp_path, "churned customer = no order in 180 days")
    content = notes.read_text(encoding="utf-8")
    assert "churned customer = no order in 180 days" in content
