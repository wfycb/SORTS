const d = db.getSiblingDB("reservation-db");
d.reservation.drop();
print("  reservation 문서수 = " + d.reservation.countDocuments({}));
