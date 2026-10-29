import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

async function noAxeViolations(page: Page, where: string) {
  // Measure what people see once things settle: contrast is wrong mid fade-in by definition.
  await page.waitForFunction(() =>
    document.getAnimations().every((a) => a.playState !== "running" || a.effect?.getTiming().iterations === Infinity),
  );
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  expect(results.violations.map((v) => `${where}: ${v.id} at ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
}

async function noSidewaysScroll(page: Page) {
  const [scroll, inner] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
  expect(scroll).toBeLessThanOrEqual(inner);
}

async function startAsAmina(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Continue as Amina" }).click();
  await expect(page).toHaveURL(/\/chat$/);
  await expect(page.getByText(/Hello Amina/)).toBeVisible();
}

async function bannerOnScreen(page: Page, where: string) {
  const box = await page.getByRole("note").filter({ hasText: "Demonstration" }).boundingBox();
  const height = page.viewportSize()?.height ?? 0;
  if (box === null) throw new Error(`${where}: no demonstration band`);
  expect(box.y, `${where}: the band is above the screen`).toBeGreaterThanOrEqual(0);
  expect(box.y + box.height, `${where}: the band is below the screen`).toBeLessThanOrEqual(height);
}

async function openRail(page: Page) {
  const toggle = page.getByRole("button", { name: /Accounts, quick actions and texts/ });
  if (await toggle.isVisible()) await toggle.click();
}

test("the home page says what this is and passes axe", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("note")).toContainText("Demonstration");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("cites its sources");
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", "noindex, nofollow");
  await noAxeViolations(page, "home");
  await noSidewaysScroll(page);
});

test("a question gets an answer with a source that opens", async ({ page }) => {
  await startAsAmina(page);
  await noAxeViolations(page, "chat");
  await page.getByRole("textbox", { name: "Your message" }).fill("How much does it cost to send KES 5,000 to mobile money?");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  const answer = page.locator(".msg--assistant").last();
  await expect(answer.getByText("From the bank's documents")).toBeVisible();
  await answer.getByRole("button", { name: /^Source 1/ }).click();
  const drawer = page.getByRole("dialog");
  await expect(drawer.getByText("Passage cited", { exact: false })).toBeVisible();
  await noAxeViolations(page, "source drawer");
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  await answer.getByRole("button", { name: "This answer helped" }).click();
  await expect(answer.getByRole("button", { name: "This answer helped" })).toHaveAttribute("aria-pressed", "true");
  await noSidewaysScroll(page);
});

test("the demonstration band stays on screen through a conversation", async ({ page }) => {
  await startAsAmina(page);
  for (const question of ["What's my balance?", "Is there a monthly fee on the Everyday Account?", "How do I raise my daily limit?"]) {
    await page.getByRole("textbox", { name: "Your message" }).fill(question);
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect(page.locator(".msg--assistant")).not.toHaveCount(0);
    await expect(page.getByRole("textbox", { name: "Your message" })).toBeEnabled();
  }
  await expect(page.locator(".msg--customer")).toHaveCount(3);
  // The conversation is taller than the screen, scrolled to its newest message.
  expect(await page.evaluate(() => window.scrollY)).toBeGreaterThan(0);
  await bannerOnScreen(page, "chat, scrolled to the newest message");

  await page.goto("/");
  await page.getByRole("button", { name: "Open as the AI lead" }).click();
  await expect(page.getByRole("heading", { name: "Audit trail" })).toBeVisible();
  await page.mouse.wheel(0, 5000);
  await bannerOnScreen(page, "console, scrolled down");
});

test("a card block waits for the customer and then runs", async ({ page }) => {
  await startAsAmina(page);
  await openRail(page);
  await page.getByRole("button", { name: "Block a card" }).click();
  await page.getByRole("button", { name: "Block for now" }).first().click();
  const slip = page.locator(".slip--confirm").last();
  await expect(slip.getByRole("heading")).toContainText("Block your");
  await noAxeViolations(page, "confirmation slip");
  await slip.getByRole("button", { name: "Confirm" }).click();
  await expect(slip.getByRole("status")).toHaveText("Done");
  await expect(page.getByText(/^Done: block your/)).toBeVisible();
});

test("a customer reaches a person, and the agent replies", async ({ page }) => {
  await startAsAmina(page);
  await openRail(page);
  await page.getByRole("button", { name: "Talk to a person" }).click();
  await expect(page.getByText("A colleague is looking after this conversation")).toBeVisible();

  await page.goto("/");
  await page.getByRole("button", { name: "Open as an agent" }).click();
  await expect(page.getByRole("heading", { name: "Handovers" })).toBeVisible();
  await expect(page.getByRole("heading", { name: /tapped Talk to a person/ })).toBeVisible();
  await noAxeViolations(page, "agent console");
  await page.getByRole("textbox", { name: "Reply to the customer" }).fill("Hello Amina, I'm here to help.");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await page.getByRole("button", { name: "Resolve and hand back" }).click();

  await page.goto("/chat");
  await expect(page.getByText("Hello Amina, I'm here to help.")).toBeVisible();
  await expect(page.getByText("The assistant can help again from here.")).toBeVisible();
});

test("the AI lead sees an intact audit trail", async ({ page }) => {
  await startAsAmina(page);
  await page.getByRole("textbox", { name: "Your message" }).fill("What's my balance?");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.locator(".msg--assistant").last()).toBeVisible();
  await page.goto("/");
  await page.getByRole("button", { name: "Open as the AI lead" }).click();
  await expect(page.getByRole("heading", { name: "Audit trail" })).toBeVisible();
  await expect(page.getByText(/Chain intact/)).toBeVisible();
  await noAxeViolations(page, "audit trail");
});

test("the AI lead's monitoring, evaluations and prompts load and pass axe", async ({ page }) => {
  await startAsAmina(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Open as the AI lead" }).click();
  const views = page.getByRole("navigation", { name: "AI lead views" });

  await views.getByRole("button", { name: "Monitoring" }).click();
  await expect(page).toHaveURL(/view=monitoring/);
  await expect(page.getByRole("heading", { name: "Monitoring" })).toBeVisible();
  await expect(page.getByRole("meter").first()).toBeVisible();
  await noAxeViolations(page, "monitoring");
  await noSidewaysScroll(page);

  await views.getByRole("button", { name: "Evaluations" }).click();
  await expect(page.getByRole("heading", { name: "Evaluations" })).toBeVisible();
  await expect(page.getByText(/Router: \d+ labelled messages/)).toBeVisible();
  await noAxeViolations(page, "evaluations");

  await views.getByRole("button", { name: "Prompts" }).click();
  await expect(page.getByRole("heading", { name: "assistant.router" })).toBeVisible();
  await page.getByRole("button", { name: "Show the text" }).first().click();
  await expect(page.locator(".payload").first()).toContainText("Kaskazi");
  await noAxeViolations(page, "prompts");
});

test("a loan application goes from documents to a person's decision", async ({ page }) => {
  await startAsAmina(page);
  await page.goto("/apply");
  await expect(page.getByRole("heading", { name: "Apply with your documents" })).toBeVisible();
  await expect(page.getByText("Each month")).toBeVisible();
  await noAxeViolations(page, "apply");
  await page.getByRole("button", { name: "Start the application" }).click();

  // A payslip with an instruction hidden in white text is caught and sent to a person.
  await page.getByRole("button", { name: /Payslip with a hidden instruction/ }).click();
  const payslip = page.locator(".doc-slot").filter({ hasText: "Your latest payslip" });
  await expect(payslip.getByText("A colleague will check it")).toBeVisible();
  await expect(payslip.getByText(/Text is hidden on the page/)).toBeVisible();
  // The clean one replaces it, and is read and checked.
  await payslip.getByRole("button", { name: "Give a different payslip" }).click();
  await payslip.getByRole("button", { name: /^Payslip, October 2026 Her latest/ }).click();
  await expect(payslip.getByText("Read and checked")).toBeVisible();
  await expect(payslip.locator(".doc-field").filter({ hasText: "Net pay" })).toContainText("KES 68,940.13");
  await page.getByRole("button", { name: /^Identity card \(specimen\)/ }).click();
  await expect(page.locator(".doc-slot").filter({ hasText: "Your identity card" }).getByText("A colleague will check it")).toBeVisible();
  await noAxeViolations(page, "apply, documents read");
  await page.getByRole("button", { name: "Send for a decision" }).click();
  await expect(page.getByText("With a colleague for a decision")).toBeVisible();

  await page.goto("/");
  await page.getByRole("button", { name: "Open as a reviewer" }).click();
  await expect(page.getByRole("heading", { name: "Loan applications" })).toBeVisible();
  await expect(page.getByText("The requested loan fits")).toBeVisible();
  await noAxeViolations(page, "reviewer");
  // The stand-in providers can't read images, so the reviewer types the card's fields.
  const card = page.locator(".doc-review").filter({ hasText: "Identity card" });
  await card.getByRole("textbox", { name: "Correct full name" }).fill("Amina Wanjiru Kariuki");
  await card.getByRole("textbox", { name: "Correct id number" }).fill("27450913");
  await card.getByRole("textbox", { name: "Correct date of birth" }).fill("1988-03-14");
  await card.getByRole("textbox", { name: "Correct date of issue" }).fill("2007-05-21");
  await card.getByRole("button", { name: "Save 4 corrections" }).click();
  await expect(card.getByText("Corrected by a colleague")).toBeVisible();
  await page.getByRole("button", { name: "Approve and pay out" }).click();
  await expect(page.getByText(/Nothing waiting/)).toBeVisible();

  await page.goto("/apply");
  await expect(page.getByText("Approved and paid out")).toBeVisible();
});

test("an unknown page is the app's own not-found page", async ({ page }) => {
  await page.goto("/no-such-page");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("There's no page here.");
  await noAxeViolations(page, "not found");
});
