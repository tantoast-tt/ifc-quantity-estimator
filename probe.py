import ifcopenshell

f = ifcopenshell.open("samples/FZK-Haus.ifc")
print("schema:", f.schema)
for t in ["IfcWall", "IfcSlab", "IfcWindow", "IfcDoor", "IfcMember", "IfcSpace"]:
    print(t, len(f.by_type(t)))