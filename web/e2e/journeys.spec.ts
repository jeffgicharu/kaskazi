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

test("an unknown page is the app's own not-found page", async ({ page }) => {
  await page.goto("/no-such-page");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("There's no page here.");
  await noAxeViolations(page, "not found");
});
