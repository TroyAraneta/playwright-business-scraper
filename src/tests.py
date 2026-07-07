import unittest

from app import (
    extract_company_name,
    extract_emails,
    extract_services,
    find_contact_links,
    parse_html,
    extract_socials,
    extract_phones,
    extract_location,
)



class ScraperHeuristicTests(unittest.TestCase):
    def make_page(self, html: str, url: str = "https://example.com"):
        return parse_html(html, url)

    def test_extracts_company_name_from_title(self):
        page = self.make_page("<title>Acme Consulting - Home</title>")
        self.assertEqual(extract_company_name(page), "Acme Consulting")

    def test_extracts_visible_and_mailto_emails(self):
        page = self.make_page(
            """
            <p>Email hello@example.com today.</p>
            <a href="mailto:sales@example.com">Sales</a>
            <script>hidden@example.com</script>
            """
        )
        self.assertEqual(
            extract_emails(page),
            ["hello@example.com", "sales@example.com"],
        )

    def test_finds_same_site_contact_links(self):
        page = self.make_page(
            """
            <a href="/contact">Contact us</a>
            <a href="https://external.test/contact">External contact</a>
            """
        )
        self.assertEqual(find_contact_links(page), ["https://example.com/contact"])

    def test_extracts_services_from_nav_and_section(self):
        page = self.make_page(
            """
            <header>
              <nav>
                <a href="/services/cloud-migration">Cloud Migration</a>
              </nav>
            </header>
            <section>
              <h2>Services</h2>
              <h3>Data Analytics</h3>
              <li>Managed Security</li>
              <li>Services</li>
              <li>© Acme Consulting</li>
              <a href="/contact">Contact</a>
            </section>
            """
        )
        self.assertEqual(
            extract_services(page),
            ["Security", "Analytics", "Cloud Migration"],
        )

    def test_extracts_service_like_headings_without_services_section(self):
        page = self.make_page(
            """
            <main>
              <h1>Growth Marketing Agency For Startups</h1>
              <h3>Strategy</h3>
              <h3>Paid Media Management</h3>
              <h3>Analytics</h3>
              <h3>Reporting</h3>
              <h3>SEO & AI SEO</h3>
              <h3>Creative</h3>
            </main>
            """
        )
        self.assertEqual(
            extract_services(page),
            [
                "PPC",
                "SEO",
                "Analytics",
                "Creative",
                "Strategy",
            ],
        )

    def test_categorizes_website_hosting_and_drops_case_study_noise(self):
        page = self.make_page(
            """
            <section>
              <h2>Services</h2>
              <h3>Websites</h3>
              <h3>Analytics</h3>
              <h3>Compliance</h3>
              <h3>Hosting Solutions</h3>
              <h3>Rodeo Austin</h3>
              <p>Page load time 35% faster on average with a 5.76% bounce rate improvement.</p>
              <h3>Web Maintenance</h3>
              <h3>Site Health/Performance Optimization</h3>
              <h3>Performance Hosting & Security</h3>
            </section>
            """
        )
        self.assertEqual(
            extract_services(page),
            ["Websites", "Hosting", "Maintenance", "Security", "Compliance", "Analytics"],
        )

    def test_categorizes_design_agency_services(self):
        page = self.make_page(
            """
            <section>
              <h2>Services</h2>
              <h3>Website Design</h3>
              <h3>SEO</h3>
              <h3>Web Development</h3>
              <h3>Mobile Design</h3>
              <h3>Brand Awareness</h3>
              <h3>Social</h3>
              <h3>Graphic Design</h3>
              <h3>Digital Marketing</h3>
              <h3>Web Maintenance</h3>
              <h3>Art Direction</h3>
              <h3>Logo Design</h3>
              <h3>Typography & Color</h3>
              <h3>Brand Guidelines</h3>
              <h3>Signage</h3>
              <h3>Packaging</h3>
            </section>
            """
        )
        self.assertEqual(
            extract_services(page),
            [
                "SEO",
                "Social Media",
                "Websites",
                "Digital Marketing",
                "Maintenance",
                "Mobile Design",
                "Branding",
                "Graphic Design",
                "Creative",
            ],
        )

    def test_extracts_social_links(self):
        page = self.make_page(
            """
            <a href="https://www.linkedin.com/company/some-corp/">LinkedIn</a>
            <a href="http://facebook.com/somecorp">Facebook</a>
            <a href="https://twitter.com/some_corp">Twitter</a>
            <a href="https://youtube.com/c/somecorp">YouTube</a>
            <a href="https://instagram.com/some.corp">Instagram</a>
            <a href="https://tiktok.com/@somecorp">TikTok</a>
            <a href="/about">About</a>
            """
        )
        socials = extract_socials(page)
        self.assertEqual(socials.get("linkedin"), "https://www.linkedin.com/company/some-corp")
        self.assertEqual(socials.get("facebook"), "http://facebook.com/somecorp")
        self.assertEqual(socials.get("twitter"), "https://twitter.com/some_corp")
        self.assertEqual(socials.get("youtube"), "https://youtube.com/c/somecorp")
        self.assertEqual(socials.get("instagram"), "https://instagram.com/some.corp")
        self.assertEqual(socials.get("tiktok"), "https://tiktok.com/@somecorp")

    def test_extracts_phone_numbers(self):
        page = self.make_page(
            """
            <p>Call us at +1 (555) 123-4567 or fax at +1-555-987-6543.</p>
            <a href="tel:+15551112222">Click to Call</a>
            """
        )
        self.assertEqual(
            extract_phones(page),
            ["+1 (555) 123-4567", "+1-555-987-6543", "+15551112222"],
        )

    def test_extracts_location_heuristics(self):
        page = self.make_page(
            """
            <div>
              <p>Our headquarters is located at 123 Main Street, Suite 400, Austin, TX 78701.</p>
            </div>
            """
        )
        self.assertEqual(
            extract_location(page),
            "Our headquarters is located at 123 Main Street, Suite 400, Austin, TX 78701.",
        )


    def test_deobfuscates_emails(self):
        page = self.make_page(
            """
            <p>Email us at: info [at] example [dot] com</p>
            <p>Or support(at)example.com</p>
            <p>Or sales at example dot com</p>
            """
        )
        self.assertEqual(
            extract_emails(page),
            ["info@example.com", "sales@example.com", "support@example.com"],
        )


if __name__ == "__main__":
    unittest.main()
