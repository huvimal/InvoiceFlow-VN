"""Exact value scoring with one-to-one line matching and explicit extra-field penalties."""
import unicodedata


def flatten(invoice):
    data = invoice.model_dump(mode="json")
    lines = data.pop("lines")
    data["line_count"] = len(lines)
    for i, line in enumerate(lines, 1):
        for name, value in line.items():
            data[f"line_{i}.{name}"] = value
    return data


def normalized(value, name):
    if isinstance(value, str):
        value = " ".join(unicodedata.normalize("NFC", value).split())
        if name in {"seller", "description"}:
            value = value.casefold()
    return value


def equal(a, b, name):
    return normalized(a, name) == normalized(b, name)


def assignment(cost):
    """Hungarian minimum-cost assignment for a square integer matrix; O(n^3)."""
    n = len(cost)
    u, v, p, way = [0]*(n+1), [0]*(n+1), [0]*(n+1), [0]*(n+1)
    for i in range(1, n+1):
        p[0], j0 = i, 0
        minimum, used = [float("inf") ]*(n+1), [False]*(n+1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], float("inf"), 0
            for j in range(1, n+1):
                if not used[j]:
                    cur = cost[i0-1][j-1]-u[i0]-v[j]
                    if cur < minimum[j]:
                        minimum[j], way[j] = cur, j0
                    if minimum[j] < delta:
                        delta, j1 = minimum[j], j
            for j in range(n+1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minimum[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    return {p[j]-1: j-1 for j in range(1, n+1)}


def compare(reference, actual):
    ref, hyp = reference.model_dump(mode="json"), actual.model_dump(mode="json")
    details = []
    def add(path, expected, got, name, kind="field"):
        details.append({"field": path, "expected": expected, "actual": got,
                        "matched": equal(expected, got, name) and kind != "missing_line", "kind": kind})
    for name in ["seller", "invoice_no", "invoice_date", "subtotal", "tax", "total"]:
        add(name, ref[name], hyp[name], name)
    r, h = ref["lines"], hyp["lines"]
    fields = ["description", "quantity", "unit_price", "amount"]
    n = max(len(r), len(h))
    costs = [[sum(not equal(r[i][f], h[j][f], f) for f in fields) if i < len(r) and j < len(h)
              else 4 if i < len(r) or j < len(h) else 0 for j in range(n)] for i in range(n)]
    matching = assignment(costs) if n else {}
    extra_lines, missing_lines = [], []
    for i, j in matching.items():
        if i < len(r) and j < len(h):
            for field in fields:
                add(f"lines[{i}].{field}", r[i][field], h[j][field], field)
        elif i < len(r):
            missing_lines.append(i)
            for field in fields:
                add(f"lines[{i}].{field}", r[i][field], None, field, "missing_line")
        else:
            extra_lines.append(j)
            for field in fields:
                # Even a wholly-null extra row incurs four errors.
                details.append({"field": f"extra_lines[{j}].{field}", "expected": None,
                                "actual": h[j][field], "matched": False, "kind": "extra_line"})
    present = [d for d in details if d["expected"] is not None]
    absent = [d for d in details if d["expected"] is None and d["kind"] == "field"]
    missing = sum(not d["matched"] and d["actual"] is None for d in present)
    wrong = sum(not d["matched"] and d["actual"] is not None for d in present)
    errors = [d for d in details if not d["matched"]]
    # Missing rows must fail even when every expected cell is null.
    exact = not errors and not missing_lines and not extra_lines
    return {"matched": sum(d["matched"] for d in details), "total_fields": len(details),
            "field_accuracy": sum(d["matched"] for d in details)/max(1, len(details)),
            "present_fields": len(present), "present_matched": sum(d["matched"] for d in present),
            "present_field_accuracy": sum(d["matched"] for d in present)/len(present) if present else None,
            "absent_fields": len(absent), "false_fills": sum(d["actual"] is not None for d in absent),
            "missing_values": missing, "wrong_values": wrong,
            "extra_lines": extra_lines, "missing_lines": missing_lines,
            "exact_invoice": exact, "differences": errors,
            "line_mapping": {str(i): j for i, j in matching.items() if i < len(r) and j < len(h)},
            "line_order_changed": any(i != j for i, j in matching.items() if i < len(r) and j < len(h))}
