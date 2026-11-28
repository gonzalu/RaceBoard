"""Which flag goes with a driver's nationality or a circuit's country.

Jolpica gives nationalities as adjectives ("British") and countries as names
("UK", "United States"). Both become the two-letter ISO code the panel draws
its flags by. Anything not listed gets no flag rather than a wrong one.
"""

NATIONALITY = {
    "American": "us", "Argentine": "ar", "Argentinian": "ar", "Australian": "au", "Austrian": "at",
    "Belgian": "be", "Brazilian": "br", "British": "gb", "Canadian": "ca", "Chinese": "cn",
    "Colombian": "co", "Czech": "cz", "Danish": "dk", "Dutch": "nl", "Finnish": "fi", "French": "fr",
    "German": "de", "Hungarian": "hu", "Indian": "in", "Indonesian": "id", "Irish": "ie", "Italian": "it",
    "Japanese": "jp", "Malaysian": "my", "Mexican": "mx", "Monegasque": "mc", "New Zealander": "nz",
    "Polish": "pl", "Portuguese": "pt", "Russian": "ru", "South African": "za", "Spanish": "es",
    "Swedish": "se", "Swiss": "ch", "Thai": "th", "Venezuelan": "ve", "Korean": "kr",
}

COUNTRY = {
    "Australia": "au", "Austria": "at", "Azerbaijan": "az", "Bahrain": "bh", "Belgium": "be",
    "Brazil": "br", "Canada": "ca", "China": "cn", "France": "fr", "Germany": "de", "Hungary": "hu",
    "India": "in", "Italy": "it", "Japan": "jp", "Korea": "kr", "Malaysia": "my", "Mexico": "mx",
    "Monaco": "mc", "Netherlands": "nl", "Portugal": "pt", "Qatar": "qa", "Russia": "ru",
    "Saudi Arabia": "sa", "Singapore": "sg", "South Africa": "za", "Spain": "es", "Sweden": "se",
    "Switzerland": "ch", "Turkey": "tr", "UAE": "ae", "United Arab Emirates": "ae", "UK": "gb",
    "United Kingdom": "gb", "USA": "us", "United States": "us", "Argentina": "ar",
}


def nation(nationality):
    return NATIONALITY.get(nationality or "")


def country(name):
    return COUNTRY.get(name or "")
