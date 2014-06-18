#fo = open("kddcup.newtestdata_10_percent_unlabeled.csv", "r")
with open("kddcup.newtestdata_10_percent_unlabeled.csv") as f:
    f.write("\n".join(map(str, floats)))

print f
