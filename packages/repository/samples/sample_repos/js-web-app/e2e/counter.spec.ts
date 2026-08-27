import { expect, test } from "@playwright/test";

test("increments the counter on click", async ({ page }) => {
  await page.goto("/");
  const button = page.getByRole("button", { name: /count:/ });
  await expect(button).toHaveTextContent("Count: 0");
  await button.click();
  await expect(button).toHaveTextContent("Count: 1");
});
