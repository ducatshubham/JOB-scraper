import asyncio
import json
import csv
import os
import time
import sys
import subprocess
import random
import re
from pathlib import Path
from urllib.parse import urlparse, urlunparse, urljoin, urlencode, parse_qs
from playwright.async_api import async_playwright

# -----------------------
# File paths
# -----------------------
cookies_path = Path("cookies_recruiter.json")
output_csv = Path("linkedin_recruiter_results.csv")

# Constants to reduce duplication
SCROLL_HEIGHT_JS = "document.body.scrollHeight"
SCROLL_BY_JS = "window.scrollBy(0, {})"
SCROLL_TO_TOP_JS = "window.scrollTo(0, 0)"
SCROLL_TO_BOTTOM_JS = "window.scrollTo(0, document.body.scrollHeight)"

# -----------------------
# Helpers for Recruiter
# -----------------------
def extract_role_from_recruiter_url(search_url):
    """Extract role/job title from LinkedIn Recruiter search URL"""
    try:
        parsed_url = urlparse(search_url)
        query_params = parse_qs(parsed_url.query)
        
        # Recruiter uses 'searchKeyword' parameter
        search_keyword = query_params.get('searchKeyword', [''])[0]
        if search_keyword:
            # Clean and format the role name
            role = search_keyword.replace('%22', '').replace('"', '').replace('%20', ' ').replace('%2520', ' ').strip()
            return role.title() if role else "Professional"
        return "Professional"
    except Exception:
        return "Professional"

def validate_recruiter_url(url):
    """Validate if URL is a LinkedIn Recruiter search URL"""
    return "linkedin.com/talent/search" in url

def ask_question(prompt_text: str) -> str:
    return input(prompt_text)

async def delay(ms: int):
    await asyncio.sleep(ms / 1000)

def save_to_csv(rows, role_name):
    # Use role name in filename
    role_clean = re.sub(r'[^\w\s-]', '', role_name).strip()
    role_clean = re.sub(r'[-\s]+', '_', role_clean)
    output_file = Path(f"linkedin_recruiter_{role_clean.lower()}_results.csv")
    
    headers = [
        "Name", "Title", "Location", "Education", "Profile URL",
        "Total Experience", "Experience Details", "Skills"
    ]
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for r in rows:
            writer.writerow({
                "Name": r.get("name", "N/A"),
                "Title": r.get("title", "N/A"),
                "Location": r.get("location", "N/A"),
                "Education": r.get("education", "N/A"),
                "Profile URL": r.get("url", ""),
                "Total Experience": r.get("total_experience", "N/A"),
                "Experience Details": r.get("experience_details", "N/A"),
                "Skills": r.get("skills", "N/A")
            })
    print(f"✅ Data saved to {output_file}")
    return output_file

def open_excel(file_path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(file_path)
        elif sys.platform == "darwin":
            subprocess.run(["open", file_path])
        else:
            subprocess.run(["xdg-open", file_path])
        print("📊 Opened Excel file.")
    except Exception as e:
        print(f"❌ Could not open Excel: {e}")

async def auto_scroll(page, step=600, max_rounds=30, wait_ms=1500):
    """Slow incremental scroll to trigger lazy-load."""
    try:
        last_height = await page.evaluate(f"() => {SCROLL_HEIGHT_JS}")
        rounds = 0
        while rounds < max_rounds:
            rounds += 1
            await page.evaluate(SCROLL_BY_JS.format(step))
            await page.wait_for_timeout(wait_ms)
            new_height = await page.evaluate(f"() => {SCROLL_HEIGHT_JS}")
            if new_height == last_height:
                await page.evaluate("window.scrollBy(0, 50);")
                await page.wait_for_timeout(wait_ms)
                new_height = await page.evaluate(f"() => {SCROLL_HEIGHT_JS}")
                if new_height == last_height:
                    break
            last_height = new_height
        print("ℹ Scrolled page to load dynamic content.")
    except Exception as e:
        print(f"❌ Failed to scroll: {e}")

def clean_recruiter_profile_url(u: str) -> str:
    """Clean recruiter profile URL - keep search context"""
    try:
        # For recruiter URLs, we keep the full URL with search context
        if "/talent/profile/" in u:
            return u
        return u
    except Exception:
        return u

# -----------------------
# Browser setup
# -----------------------
async def setup_browser(playwright):
    browser = await playwright.chromium.launch(
        headless=False,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--window-size=1920,1080",
            "--disable-dev-shm-usage"
        ]
    )
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36",
        viewport={"width": 1366, "height": 768}
    )
    page = await context.new_page()

    if cookies_path.exists():
        try:
            cookies = json.loads(cookies_path.read_text(encoding="utf-8"))
            await context.add_cookies(cookies)
            print("✅ Loaded recruiter cookies from file.")
        except Exception as e:
            print(f"❌ Failed to load cookies: {e}")

    # Try to load LinkedIn first
    try:
        print("🔄 Loading LinkedIn...")
        await page.goto("https://www.linkedin.com/feed/", timeout=90000)
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(3000)
        print("✅ LinkedIn loaded successfully.")
    except Exception as e:
        print(f"❌ Failed to load LinkedIn: {e}")

    # Check if we need to login
    current_url = page.url
    if "/login" in current_url or "challenge" in current_url or "/checkpoint" in current_url or "/uas/login" in current_url:
        print("👉 Please log in manually with your LinkedIn account in the opened browser window...")
        print("🔑 Make sure you have access to LinkedIn Recruiter!")
        print("🔑 After login, navigate to LinkedIn Recruiter or just stay on the feed page")
        ask_question("🔑 Press Enter after you've successfully logged in...")
        
        # Save cookies after login
        cookies = await context.cookies()
        cookies_path.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
        print("💾 Login session saved!")
        
        # Wait a bit more after login
        await page.wait_for_timeout(5000)

    # Test Recruiter access
    try:
        print("🔄 Testing LinkedIn Recruiter access...")
        await page.goto("https://www.linkedin.com/talent/home", timeout=60000)
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(5000)
        
        current_url = page.url
        if "/uas/login" in current_url or "/login" in current_url:
            print("❌ LinkedIn Recruiter access denied. You may not have Recruiter permissions.")
            print("🔄 Continuing with regular LinkedIn access...")
            await page.goto("https://www.linkedin.com/feed/", timeout=60000)
            await page.wait_for_load_state("domcontentloaded")
        else:
            print("✅ LinkedIn Recruiter access confirmed!")
            
    except Exception as e:
        print(f"⚠️ Recruiter test failed: {e}")
        print("🔄 Falling back to regular LinkedIn...")
        try:
            await page.goto("https://www.linkedin.com/feed/", timeout=60000)
            await page.wait_for_load_state("domcontentloaded")
        except Exception as e2:
            print(f"❌ Failed to load LinkedIn: {e2}")

    return browser, context, page

# -----------------------
# Scrape Education for Recruiter - UPDATED
# -----------------------
async def scrape_recruiter_education(page, profile_url):
    try:
        education_data = await page.evaluate(r"""() => {
            let education = "";
            
            // Updated selectors based on DOM structure
            const educationSelectors = [
                '.text-highlighter__text[data-test-text-highlighter-text-only]',
                'h2[data-test-expandable-list-title]:contains("Education") ~ * .text-highlighter__text',
                '.text-highlighter__text',
                '[data-test-education]',
                '.education-section',
                '.profile-education'
            ];
            
            // First, look for education section specifically
            const educationHeaders = document.querySelectorAll('h2[data-test-expandable-list-title]');
            for (const header of educationHeaders) {
                if (header.textContent && header.textContent.includes('Education')) {
                    // Found education section, look for institution names nearby
                    let parent = header.parentElement;
                    if (parent) {
                        const textElements = parent.querySelectorAll('.text-highlighter__text[data-test-text-highlighter-text-only]');
                        for (const el of textElements) {
                            const text = el.textContent.trim();
                            if (text && (text.includes('University') || text.includes('College') || text.includes('Institute') || text.includes('School'))) {
                                education = text;
                                break;
                            }
                        }
                    }
                }
                if (education) break;
            }
            
            // Fallback to general text search
            if (!education) {
                const textElements = document.querySelectorAll('.text-highlighter__text[data-test-text-highlighter-text-only]');
                for (const el of textElements) {
                    const text = el.textContent.trim();
                    if (text && (text.includes('University') || text.includes('College') || text.includes('Institute'))) {
                        education = text;
                        break;
                    }
                }
            }
            
            return education || "N/A";
        }""")

        return education_data if education_data and education_data != "N/A" else "Limited in Recruiter"

    except Exception as e:
        print(f"❌ Failed to scrape education for {profile_url}: {e}")
        return "N/A"

# -----------------------
# Scrape Skills for Recruiter - UPDATED
# -----------------------
async def scrape_recruiter_skills(page, profile_url):
    try:
        skills = await page.evaluate(r"""() => {
            const skillsList = [];
            
            // Look for skills section based on DOM structure
            const skillHeaders = document.querySelectorAll('h2[data-test-expandable-list-title]');
            for (const header of skillHeaders) {
                if (header.textContent && header.textContent.includes('Skills')) {
                    // Found skills section, look for skill names
                    let parent = header.parentElement;
                    while (parent && !parent.classList.contains('experience-section')) {
                        parent = parent.parentElement;
                    }
                    
                    if (parent) {
                        // Look for skill entities with the specific selector
                        const skillElements = parent.querySelectorAll('dt[data-test-skill-entity-skill-name]');
                        skillElements.forEach(el => {
                            const skillText = el.textContent && el.textContent.trim();
                            if (skillText && skillText.length > 1 && skillText.length < 100) {
                                skillsList.push(skillText);
                            }
                        });
                        
                        // Also try alternative selectors
                        if (skillsList.length === 0) {
                            const altSkillElements = parent.querySelectorAll('.skill-entity__skill-name, [title][data-test-skill-entity-skill-name]');
                            altSkillElements.forEach(el => {
                                const skillText = el.textContent && el.textContent.trim();
                                if (skillText && skillText.length > 1 && skillText.length < 100) {
                                    skillsList.push(skillText);
                                }
                            });
                        }
                    }
                    break;
                }
            }
            
            // Remove duplicates and return
            return [...new Set(skillsList)];
        }""")

        return skills if skills and len(skills) > 0 else ["Limited in Recruiter"]

    except Exception as e:
        print(f"❌ Failed to scrape skills for {profile_url}: {e}")
        return ["N/A"]

# -----------------------
# Scrape Experience for Recruiter - UPDATED
# -----------------------
async def scrape_recruiter_experience(page, profile_url):
    try:
        experience_data = await page.evaluate(r"""() => {
            const experiences = [];
            let totalExperience = "N/A";

            // Look for total experience first - based on DOM structure
            const totalExpElement = document.querySelector('.t-14.t-black--light[data-test-grouped-position-entity-date-overall-range]');
            if (totalExpElement) {
                totalExperience = totalExpElement.textContent.trim();
            }

            // Look for experience section
            const experienceHeaders = document.querySelectorAll('h2[data-test-expandable-list-title]');
            for (const header of experienceHeaders) {
                if (header.textContent && header.textContent.includes('Experience')) {
                    // Found experience section, look for position details
                    let parent = header.parentElement;
                    while (parent && parent.tagName !== 'SECTION') {
                        parent = parent.parentElement;
                    }
                    
                    if (parent) {
                        // Look for grouped position entities or individual experience items
                        const expElements = parent.querySelectorAll('[data-test-grouped-position-entity], .experience-item, .pv-entity__summary-info');
                        
                        expElements.forEach(item => {
                            try {
                                let title = "N/A";
                                let company = "N/A";
                                let duration = "N/A";
                                
                                // Try to extract title, company and duration from various selectors
                                const titleEl = item.querySelector('h3, .pv-entity__summary-info-v2 h3, [data-test-job-title], .t-bold');
                                const companyEl = item.querySelector('.pv-entity__secondary-title, [data-test-company], .t-14');
                                const durationEl = item.querySelector('[data-test-grouped-position-entity-date-range], .pv-entity__bullet-item-v2, [data-test-duration]');
                                
                                if (titleEl) {
                                    title = titleEl.textContent.trim();
                                }
                                if (companyEl) {
                                    company = companyEl.textContent.trim();
                                }
                                if (durationEl) {
                                    duration = durationEl.textContent.trim();
                                }
                                
                                if (title !== "N/A" || company !== "N/A") {
                                    experiences.push({
                                        title: title,
                                        company: company,
                                        duration: duration
                                    });
                                }
                            } catch (e) {
                                console.log('Error parsing experience item:', e);
                            }
                        });
                    }
                    break;
                }
            }

            return {
                experiences: experiences,
                totalExperience: totalExperience
            };
        }""")

        return experience_data

    except Exception as e:
        print(f"❌ Failed to scrape experience for {profile_url}: {e}")
        return {
            "experiences": [],
            "totalExperience": "N/A"
        }

# -----------------------
# Scrape Recruiter Profile - UPDATED
# -----------------------
async def scrape_recruiter_profile(page, profile_url):
    try:
        url = clean_recruiter_profile_url(profile_url)
        print(f"🔍 Navigating to recruiter profile: {url}")
        await page.goto(url, timeout=90000)
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(5000)
        
        # Scroll to load content
        await auto_scroll(page, step=500, max_rounds=10, wait_ms=2000)
        await page.wait_for_timeout(3000)

        # Extract basic profile information - UPDATED based on DOM structure
        basic_data = await page.evaluate(r"""() => {
            const getText = (selectors) => {
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el && el.innerText && el.innerText.trim()) return el.innerText.trim();
                }
                return "N/A";
            };

            // Updated selectors based on DOM structure provided
            const name = getText([
                '.artdeco-entity-lockup__title.ember-view', // Exact selector from DOM
                'div[id*="ember"].artdeco-entity-lockup__title',
                '.artdeco-entity-lockup__title',
                'h1',
                '.profile-topcard__name',
                '.profile-name'
            ]);
            
            const title = getText([
                'span[data-test-row-lockup-headline] em.sh', // Exact selector for highlighted title
                'span[data-test-row-lockup-headline]', // Full headline
                'em.sh', // Just the highlighted part
                '.profile-topcard__headline',
                '.profile-title',
                '.artdeco-entity-lockup__subtitle'
            ]);
            
            const location = getText([
                'div[data-test-row-lockup-location]', // Exact selector from DOM
                'div[data-live-test-row-lockup-location]',
                '.profile-topcard__location',
                '.profile-location'
            ]);

            return {
                name: name !== "N/A" ? name : "Name not found",
                title: title !== "N/A" ? title : "Title not found", 
                location: location !== "N/A" ? location.replace('·', '').trim() : "Location not found"
            };
        }""")

        # Get additional profile data
        education_data = await scrape_recruiter_education(page, url)
        experience_data = await scrape_recruiter_experience(page, url)
        skills_data = await scrape_recruiter_skills(page, url)

        # Format experience details
        experience_details = []
        for exp in (experience_data.get("experiences") or []):
            detail = f"{exp.get('company','N/A')} | {exp.get('title','N/A')} | {exp.get('duration','N/A')}"
            experience_details.append(detail)
        experience_details_str = " || ".join(experience_details)

        # Format skills
        skills_str = " | ".join(skills_data) if skills_data else "N/A"

        result = {
            "name": basic_data.get("name", "N/A"),
            "title": basic_data.get("title", "N/A"),
            "location": basic_data.get("location", "N/A"),
            "education": education_data,
            "url": url,
            "total_experience": experience_data.get("totalExperience", "Limited in Recruiter"),
            "experience_details": experience_details_str if experience_details_str else "Limited in Recruiter",
            "skills": skills_str
        }
        
        print(f"✅ Scraped recruiter profile: {result['name']} - {result['title']}")
        return result

    except Exception as e:
        print(f"❌ Failed to scrape recruiter profile {profile_url}: {e}")
        return {
            "name": "Failed to scrape", 
            "title": "N/A", 
            "location": "N/A",
            "education": "N/A", 
            "url": clean_recruiter_profile_url(profile_url),
            "total_experience": "N/A", 
            "experience_details": "N/A",
            "skills": "N/A"
        }

# -----------------------
# Collect Profile URLs from LinkedIn Recruiter Search Results
# -----------------------
async def collect_recruiter_profile_urls(page, search_url, limit, role_name):
    profile_urls = set()
    print(f"🔍 Starting to collect {limit} {role_name} profiles from LinkedIn Recruiter: {search_url}")

    try:
        await page.goto(search_url, timeout=90000)
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(8000)  # Longer wait for recruiter
        
        # Check if redirected to login
        current_url = page.url
        if "/uas/login" in current_url or "/login" in current_url or "/checkpoint" in current_url:
            print("❌ Redirected to login page. LinkedIn Recruiter access required.")
            print("🔑 Please ensure you:")
            print("   1. Are logged in with correct credentials")
            print("   2. Have LinkedIn Recruiter access")
            print("   3. The search URL is valid and accessible")
            return []
            
    except Exception as e:
        print(f"❌ Failed to load search page: {e}")
        print("🔍 This might be due to:")
        print("   - No LinkedIn Recruiter access")
        print("   - Invalid search URL")
        print("   - Network issues")
        return []

    max_attempts = 50
    attempt = 0
    no_new_profiles_count = 0
    
    while attempt < max_attempts and len(profile_urls) < limit:
        attempt += 1
        previous_count = len(profile_urls)
        
        print(f"🔄 Collection attempt {attempt}/{max_attempts} - {role_name} profiles found: {len(profile_urls)}")
        
        # Scroll to load more profiles
        await auto_scroll(page, step=1500, max_rounds=25, wait_ms=2000)
        await page.wait_for_timeout(5000)

        # Debug: Check page content
        page_content = await page.evaluate(r"""() => {
            return {
                url: window.location.href,
                title: document.title,
                hasProfiles: document.querySelectorAll('a[href*="/talent/profile/"]').length > 0,
                profileCount: document.querySelectorAll('a[href*="/talent/profile/"]').length
            };
        }""")
        print(f"📊 Page debug: {page_content}")

        # Collect profile URLs from LinkedIn Recruiter
        new_urls = await page.evaluate(r"""() => {
            const profileUrls = [];
            
            // Recruiter-specific profile link patterns
            const profileLinkSelectors = [
                'a[href*="/talent/profile/"]',
                '[data-test-link-to-profile-link="true"]',
                '[data-live-test-link-to-profile-link="true"]',
                'a[data-test-link-to-profile-link]',
                'a[data-live-test-link-to-profile-link]'
            ];
            
            profileLinkSelectors.forEach(selector => {
                const links = document.querySelectorAll(selector);
                links.forEach(link => {
                    const href = link.href || link.getAttribute("href") || "";
                    if (href && href.includes("/talent/profile/") && 
                        !href.includes("/company/") &&
                        !href.includes("/school/")) {
                        profileUrls.push(href);
                    }
                });
            });
            
            // Also look for candidate cards and extract profile links
            const candidateCards = document.querySelectorAll('[data-test-member-card], .candidate-card, .search-result-card');
            candidateCards.forEach(card => {
                const profileLink = card.querySelector('a[href*="/talent/profile/"]');
                if (profileLink) {
                    const href = profileLink.href || profileLink.getAttribute("href");
                    if (href) {
                        profileUrls.push(href);
                    }
                }
            });
            
            // Remove duplicates and return
            const uniqueUrls = [...new Set(profileUrls)];
            console.log(`Found ${uniqueUrls.length} profile URLs on page`);
            return uniqueUrls;
        }""")

        for url in new_urls:
            if url:
                profile_urls.add(url)

        # LinkedIn Recruiter - Next Page Navigation
        clicked_next = False
        try:
            next_button_selectors = [
                'button[aria-label="Next"]',  # Common on new UIs
                'button[aria-label="Go to next page"]',
                'a[data-test-pagination-next]',
                'a[data-live-test-pagination-next]',
                'a.pagination__quick-link--next',
                'a[rel="next"]',
                'a[aria-label*="Go to next page"]',
                'button.artdeco-pagination__button--next'
            ]
            
            for selector in next_button_selectors:
                try:
                    next_element = await page.query_selector(selector)
                    if next_element:
                        if await next_element.is_disabled():
                            print(f"ℹ️ Next button found but is disabled ('{selector}').")
                            continue

                        if await next_element.is_visible():
                            print(f"➡️ Found next button ('{selector}'). Clicking...")
                            await next_element.click()
                            await page.wait_for_load_state("domcontentloaded", timeout=15000)
                            await page.wait_for_timeout(random.randint(5000, 8000))
                            clicked_next = True
                            print("✅ Clicked next page and waited.")
                            break  # Exit selector loop
                except Exception as e:
                    print(f"⚠️ Could not use selector '{selector}': {e}")
                    continue
            
            if not clicked_next:
                print("🤷 Could not find or click a 'Next' button.")

        except Exception as e:
            print(f"❌ Next button navigation failed: {e}")

        new_profiles_found = len(profile_urls) - previous_count
        print(f"📊 Found {new_profiles_found} new {role_name} profiles. Total profiles: {len(profile_urls)}")

        if new_profiles_found == 0:
            no_new_profiles_count += 1
        else:
            no_new_profiles_count = 0

        if no_new_profiles_count >= 5:  # Reduced threshold for recruiter
            print("🔄 No new profiles found. Trying different scroll pattern...")
            await page.evaluate(SCROLL_TO_TOP_JS)
            await page.wait_for_timeout(4000)
            await page.evaluate(SCROLL_TO_BOTTOM_JS)
            await page.wait_for_timeout(6000)
            
            # Try clicking "Show more" or "Load more" buttons
            try:
                load_more_selectors = [
                    "button:has-text('Show more')",
                    "button:has-text('Load more')",
                    "[data-test-load-more]",
                    ".artdeco-button--secondary"
                ]
                for selector in load_more_selectors:
                    load_more_btn = await page.query_selector(selector)
                    if load_more_btn and await load_more_btn.is_visible():
                        print("🔄 Clicking 'Show more' button...")
                        await load_more_btn.click()
                        await page.wait_for_timeout(5000)
                        break
            except Exception:
                pass
                
            no_new_profiles_count = 0

        if len(profile_urls) >= limit:
            print(f"✅ Collected enough {role_name} profiles: {len(profile_urls)}")
            break

        # Longer delay for recruiter to avoid rate limiting
        await delay(6000 + random.randint(4000, 8000))

    final_list = list(profile_urls)[:limit]
    print(f"🎯 Final collection: {len(final_list)} {role_name} profiles")
    
    return final_list

# -----------------------
# Main execution function for Recruiter
# -----------------------
async def main():
    async with async_playwright() as p:
        browser, context, page = await setup_browser(p)

        print("📝 LinkedIn Recruiter Profile Scraper")
        print("📝 Please provide the LinkedIn Recruiter talent search URL")
        print("Example: https://www.linkedin.com/talent/search?searchContextId=...&searchKeyword=...")
        
        search_url = ask_question("🔗 Enter the LinkedIn Recruiter search results URL: ").strip()
        if not search_url:
            print("❌ URL is required. Exiting.")
            await browser.close()
            return

        # Validate Recruiter URL format
        if not validate_recruiter_url(search_url):
            print("❌ Please provide a valid LinkedIn Recruiter talent search URL")
            print("Make sure the URL contains '/talent/search'")
            await browser.close()
            return

        # Extract role from Recruiter URL
        role_name = extract_role_from_recruiter_url(search_url)
        print(f"🎯 Detected role: {role_name}")

        try:
            limit = int(ask_question(f"🔢 How many {role_name} profiles to scrape? (default: 10): ").strip() or "10")
        except Exception:
            limit = 10

        print(f"🎯 Target URL: {search_url}")

        # Test access to the search URL first
        print("🔍 Testing access to search URL...")
        try:
            test_response = await page.goto(search_url, timeout=60000)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(3000)
            
            current_url = page.url
            if "/uas/login" in current_url or "/login" in current_url:
                print("❌ Search URL requires authentication or Recruiter access.")
                print("🔑 Options:")
                print("   1. Make sure you're logged in with the correct LinkedIn account")
                print("   2. Verify you have LinkedIn Recruiter access")
                print("   3. Try logging in manually in the browser")
                
                ask_question("Press Enter to try manual login, or Ctrl+C to exit...")
                
                # Try manual login flow
                await page.goto("https://www.linkedin.com/login", timeout=60000)
                print("👉 Please log in manually in the browser window...")
                ask_question("🔑 Press Enter after successful login...")
                
                # Save new cookies
                cookies = await context.cookies()
                cookies_path.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
                print("💾 New login session saved!")
                
                # Test search URL again
                await page.goto(search_url, timeout=60000)
                await page.wait_for_load_state("domcontentloaded")
                await page.wait_for_timeout(5000)
                
                if "/uas/login" in page.url or "/login" in page.url:
                    print("❌ Still unable to access the search URL.")
                    print("This usually means you don't have LinkedIn Recruiter access.")
                    await browser.close()
                    return
                    
            print("✅ Search URL accessible!")
                
        except Exception as e:
            print(f"❌ Failed to access search URL: {e}")
            await browser.close()
            return

        # Collect profile URLs from the recruiter search results
        urls = await collect_recruiter_profile_urls(page, search_url, limit, role_name)
        
        if not urls:
            print(f"❌ No {role_name} profile URLs found.")
            print("🔍 This could be due to:")
            print("   - No search results on the page")
            print("   - Different page structure than expected") 
            print("   - Access restrictions")
            print("\n🔧 Debugging info:")
            
            # Add debugging information
            try:
                page_title = await page.title()
                current_url = page.url
                print(f"   - Current page title: {page_title}")
                print(f"   - Current URL: {current_url}")
                
                # Check if there are any profile-like elements
                profile_elements = await page.evaluate("""() => {
                    const profiles = document.querySelectorAll('a[href*="/in/"], a[href*="/talent/profile/"]');
                    return profiles.length;
                }""")
                print(f"   - Profile-like elements found: {profile_elements}")
                
            except Exception as debug_e:
                print(f"   - Debug info error: {debug_e}")
                
            await browser.close()
            return

        print(f"🎯 Starting to scrape {len(urls)} {role_name} profiles from Recruiter...")
        results = []
        
        for i, url in enumerate(urls, 1):
            print(f"\n🔍 [{i}/{len(urls)}] Scraping {role_name} recruiter profile: {url}")
            try:
                profile_data = await scrape_recruiter_profile(page, url)
                results.append(profile_data)
                
                if i < len(urls):
                    delay_time = 8000 + random.randint(4000, 10000)  # Longer delay for recruiter
                    print(f"⏳ Waiting {delay_time/1000:.1f}s before next profile...")
                    await delay(delay_time)
                    
            except Exception as e:
                print(f"❌ Failed to scrape recruiter profile {url}: {e}")
                results.append({
                    "name": "Failed to scrape", 
                    "title": "N/A", 
                    "location": "N/A",
                    "education": "N/A", 
                    "url": url,
                    "total_experience": "N/A", 
                    "experience_details": "N/A",
                    "skills": "N/A"
                })

        # Save results to CSV
        if results:
            output_file = save_to_csv(results, f"Recruiter_{role_name}")
            open_excel(output_file)
            
            print(f"\n🎉 LinkedIn Recruiter {role_name} Profile Scraping completed!")
            print(f"📊 Total {role_name} profiles scraped: {len(results)}")
            print(f"📁 Results saved to: {output_file}")
        else:
            print("❌ No data to save.")

        await browser.close()

# Entry point
# -----------------------
if __name__ == "__main__":
    print("🚀 LinkedIn Recruiter Dynamic Profile Scraper")
    print("=" * 70)
    print("📝 This script works with LinkedIn Recruiter talent search URLs")
    print("📝 Example: https://www.linkedin.com/talent/search?searchContextId=...&searchKeyword=...")
    print("📝 Requirements:")
    print("   - LinkedIn Recruiter account access")
    print("   - Valid talent search URL from LinkedIn Recruiter")
    print("   - Proper login credentials")
    print("📝 Make sure to apply your desired filters in Recruiter first, then copy the URL")
    print("=" * 70)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⏹️ Scraping interrupted by user.")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")

    print("\n👋 Thanks for using the LinkedIn Recruiter Profile scraper!")