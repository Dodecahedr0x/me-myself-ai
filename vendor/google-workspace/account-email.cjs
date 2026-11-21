// Read the current principal using a service the account has already authorized.
module.exports = async function accountEmail(authManager) {
  const client = await authManager.getAuthenticatedClient();
  const sources = [
    ["https://www.googleapis.com/drive/v3/about", "user(emailAddress)", data => data.user?.emailAddress],
    ["https://gmail.googleapis.com/gmail/v1/users/me/profile", "emailAddress", data => data.emailAddress],
    ["https://www.googleapis.com/calendar/v3/calendars/primary", "id", data => data.id],
  ];
  for (const [url, fields, readEmail] of sources) {
    try {
      const {data} = await client.request({url, params: {fields}, timeout: 5000});
      const email = readEmail(data);
      if (typeof email === "string" && /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
        return {content: [{type: "text", text: JSON.stringify({email})}]};
      }
    } catch {
      // A partial grant can still identify the account through another service.
    }
  }
  return {content: [{type: "text", text: "{}"}]};
};
