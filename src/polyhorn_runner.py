from polyqent.main import execute

# Execute PolyQnt solver
is_sat, model = execute(
    formula="./tmp/temporary_polyhorn_input.smt2",
    config="./tmp/temporary_polyhorn_config.json",
)

print("\nis_sat:")
print(is_sat)
print("\nmodel:")
print(model)