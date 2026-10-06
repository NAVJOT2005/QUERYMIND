"""Regenerate demo/01_shop.sql (deterministic). Run: python scripts/gen_demo_sql.py"""
import random
from datetime import date, datetime, timedelta
from pathlib import Path

random.seed(42)
OUT = Path(__file__).resolve().parent.parent / "demo" / "01_shop.sql"

SCHEMA = """\
DROP DATABASE IF EXISTS shop;
CREATE DATABASE shop CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE shop;

CREATE TABLE categories (
  id INT PRIMARY KEY,
  name VARCHAR(60) NOT NULL,
  parent_id INT NULL COMMENT 'NULL for top-level categories',
  CONSTRAINT fk_cat_parent FOREIGN KEY (parent_id) REFERENCES categories(id)
) COMMENT='Two-level product category tree';

CREATE TABLE customers (
  id INT PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  email VARCHAR(150) NOT NULL,
  country CHAR(2) NOT NULL COMMENT 'ISO 3166-1 alpha-2, e.g. US, DE',
  city VARCHAR(80),
  segment ENUM('consumer','business') NOT NULL,
  signup_date DATE NOT NULL
);

CREATE TABLE products (
  id INT PRIMARY KEY,
  name VARCHAR(120) NOT NULL,
  category_id INT NOT NULL,
  price DECIMAL(10,2) NOT NULL COMMENT 'current list price in USD',
  stock INT NOT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1 COMMENT '1 = currently sellable',
  CONSTRAINT fk_prod_cat FOREIGN KEY (category_id) REFERENCES categories(id)
);

CREATE TABLE orders (
  id INT PRIMARY KEY,
  customer_id INT NOT NULL,
  order_date DATETIME NOT NULL,
  status ENUM('pending','paid','shipped','delivered','cancelled','refunded') NOT NULL
    COMMENT 'cancelled and refunded orders do not count as revenue',
  shipping_country CHAR(2) NOT NULL,
  CONSTRAINT fk_ord_cust FOREIGN KEY (customer_id) REFERENCES customers(id),
  INDEX idx_orders_date (order_date)
);

CREATE TABLE order_items (
  id INT PRIMARY KEY,
  order_id INT NOT NULL,
  product_id INT NOT NULL,
  quantity INT NOT NULL,
  unit_price DECIMAL(10,2) NOT NULL COMMENT 'price paid per unit at purchase time (may differ from products.price)',
  discount_pct DECIMAL(4,2) NOT NULL DEFAULT 0 COMMENT 'percent off, 0-100',
  CONSTRAINT fk_item_order FOREIGN KEY (order_id) REFERENCES orders(id),
  CONSTRAINT fk_item_prod FOREIGN KEY (product_id) REFERENCES products(id)
);

CREATE TABLE reviews (
  id INT PRIMARY KEY,
  product_id INT NOT NULL,
  customer_id INT NOT NULL,
  rating TINYINT NOT NULL COMMENT '1 (worst) to 5 (best)',
  created_at DATETIME NOT NULL,
  CONSTRAINT fk_rev_prod FOREIGN KEY (product_id) REFERENCES products(id),
  CONSTRAINT fk_rev_cust FOREIGN KEY (customer_id) REFERENCES customers(id)
);
"""

def q(v):
    if v is None: return "NULL"
    if isinstance(v, (int, float)): return str(v)
    return "'" + str(v).replace("'", "''") + "'"

def insert(table, rows, chunk=200):
    out = []
    for i in range(0, len(rows), chunk):
        vals = ",\n".join("(" + ",".join(q(x) for x in r) + ")" for r in rows[i:i+chunk])
        out.append(f"INSERT INTO {table} VALUES\n{vals};")
    return "\n".join(out)

parents = ["Electronics", "Home & Kitchen", "Sports & Outdoors"]
children = {"Electronics": ["Phones", "Laptops", "Audio"], "Home & Kitchen": ["Cookware", "Furniture"],
            "Sports & Outdoors": ["Fitness", "Camping"]}
cats, cid, child_ids = [], 1, []
for p in parents:
    pid = cid; cats.append((cid, p, None)); cid += 1
    for c in children[p]:
        cats.append((cid, c, pid)); child_ids.append(cid); cid += 1

countries = [("US","New York"),("US","Austin"),("DE","Berlin"),("DE","Munich"),("GB","London"),
             ("IN","Mumbai"),("IN","Bengaluru"),("BR","Sao Paulo"),("CA","Toronto"),("AU","Sydney"),("FR","Paris")]
first = "Ava Liam Noah Emma Olivia Mia Lucas Ethan Aria Zoe Kai Ravi Priya Anya Hans Lena Chen Mei Omar Sofia".split()
last = "Smith Patel Muller Garcia Brown Singh Rossi Silva Kim Nguyen Jones Khan Weber Costa Evans".split()
customers = []
for i in range(1, 121):
    c, city = random.choice(countries)
    n = f"{random.choice(first)} {random.choice(last)}"
    customers.append((i, n, f"{n.lower().replace(' ','.')}{i}@example.com", c, city,
                      random.choices(["consumer","business"], [0.75,0.25])[0],
                      (date(2023,1,1)+timedelta(days=random.randint(0,800))).isoformat()))

nouns = ["Pro","Max","Lite","Plus","Air","Ultra","Mini","Classic"]
products = []
for i in range(1, 41):
    cat = random.choice(child_ids)
    products.append((i, f"{cats[cat-1][1][:-1] if cats[cat-1][1].endswith('s') else cats[cat-1][1]} {random.choice(nouns)} {i}",
                     cat, round(random.uniform(8, 900), 2), random.randint(0, 300), random.choices([1,0],[0.92,0.08])[0]))
price = {p[0]: p[3] for p in products}

statuses = ["pending","paid","shipped","delivered","cancelled","refunded"]
weights = [0.05,0.1,0.15,0.55,0.1,0.05]
orders, items, iid = [], [], 1
for oid in range(1, 801):
    cust = random.choice(customers)
    when = datetime(2024,1,1) + timedelta(days=random.randint(0,720), seconds=random.randint(0,86399))
    ship = cust[3] if random.random() < 0.9 else random.choice(countries)[0]
    orders.append((oid, cust[0], when.strftime("%Y-%m-%d %H:%M:%S"), random.choices(statuses, weights)[0], ship))
    for _ in range(random.choices([1,2,3,4],[0.45,0.3,0.15,0.1])[0]):
        pid = random.randint(1, 40)
        up = round(price[pid] * random.uniform(0.9, 1.05), 2)
        items.append((iid, oid, pid, random.randint(1,4), up, random.choice([0,0,0,5,10,15,20])))
        iid += 1

reviews = []
for rid in range(1, 301):
    when = datetime(2024,1,1) + timedelta(days=random.randint(0,720), seconds=random.randint(0,86399))
    reviews.append((rid, random.randint(1,40), random.randint(1,120),
                    random.choices([1,2,3,4,5],[0.06,0.08,0.16,0.35,0.35])[0], when.strftime("%Y-%m-%d %H:%M:%S")))

OUT.write_text("\n\n".join([SCHEMA, insert("categories", cats), insert("customers", customers),
    insert("products", products), insert("orders", orders), insert("order_items", items),
    insert("reviews", reviews)]) + "\n")
print(f"wrote {OUT} ({OUT.stat().st_size//1024} KB): {len(customers)} customers, {len(orders)} orders, {len(items)} items")
