const {test} = require("node:test");
const assert = require("node:assert/strict");
const identify = require("../vendor/google-workspace/account-email.cjs");

for (const [service, expected, response] of [
  ["Drive", "https://www.googleapis.com/drive/v3/about", {user: {emailAddress: "work@example.test"}}],
  ["Gmail", "https://gmail.googleapis.com/gmail/v1/users/me/profile", {emailAddress: "work@example.test"}],
  ["Calendar", "https://www.googleapis.com/calendar/v3/calendars/primary", {id: "work@example.test"}],
]) {
  test(service + " identifies the current account under partial consent", async () => {
    const result = await identify({getAuthenticatedClient: async () => ({
      request: async ({url}) => {
        if (url !== expected) throw Error("scope denied");
        return {data: response};
      }
    })});
    assert.deepEqual(JSON.parse(result.content[0].text), {email: "work@example.test"});
  });
}

test("a display name or shared calendar listing cannot identify an account", async () => {
  const result = await identify({getAuthenticatedClient: async () => ({
    request: async () => ({data: {id: "primary", items: [{id: "other@example.test"}]}})
  })});
  assert.deepEqual(JSON.parse(result.content[0].text), {});
});
