with open("backend/tests/test_reporting_stock_service.py", "r") as f:
    lines = f.readlines()

new_lines = []
skip = False
for i, line in enumerate(lines):
    if "assert res_comb['cost_of_goods_sold']['closing_stock'] is None" in line:
        if skip:
            continue
        skip = True
    new_lines.append(line)

with open("backend/tests/test_reporting_stock_service.py", "w") as f:
    f.writelines(new_lines)
