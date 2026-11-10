// The tips shown beside controls when "Show tips" is on (the product-tour overlay in product-tour.js).
// Each page puts its screen key on <body data-screen>, and pages whose controls depend on state carry the state in
// the key (plan-none / plan-open / plan-search / plan-ask / plan-approved, records-empty / -check / -done, signoff-open / -done), so a
// tip never points at a control the page is not showing. A tip with no `screen` shows on every page.
//
// Keep these true: when a control changes, change its tip in the same edit; when it goes, delete its entry. A tip whose
// control is missing shows as "N tips need updating" in the bar and warns in the console.
(function () {
  // helper: the same tip on several screen keys
  function on(screens, tips) {
    var out = [];
    screens.forEach(function (s) { tips.forEach(function (t) { out.push(Object.assign({ screen: s }, t)); }); });
    return out;
  }

  var tips = [
    // every page
    { token: 'help', text: 'Opens the help page: what the tool is, each of the five steps, and a start-to-finish test walkthrough.' },
    { token: 'models', text: 'Which AI models can answer right now. A greyed one is offline and cannot be chosen on any step.' },
    { token: 'cost-chip', text: 'What the AI calls on this request have cost so far, from the real token counts each model reported. Hover for the breakdown by step.' },

    // home
    { screen: 'home', token: 'home-new', text: 'Start a new request. You paste or upload the request on the next page.' },

    // help
    { screen: 'help', token: 'help-checklist', text: 'Tick each item as you confirm it. Ticks are saved in this browser only.' },

    // step 1: request
    { screen: 'intake', token: 'intake-text', text: 'Paste the request as it came in: the email, the form text, or notes from a call.' },
    { screen: 'intake', token: 'intake-sample', text: 'Fills the box with the fictional band trip request, so you can try the tool without real data.' },
    { screen: 'intake', token: 'intake-file', text: 'Or upload the request as a saved email (.eml) or a .txt file instead of pasting it.' },
    { screen: 'intake', token: 'intake-contact', text: 'The requester’s email goes here. It fills the To line if you email them from step 2.' },
    { screen: 'intake', token: 'intake-model', text: 'Which AI drafts the search plan. The request has no student records, so Claude is fine here.' },
    { screen: 'intake', token: 'intake-go', text: 'Saves the request and drafts the Vault search plan. Takes 30 to 40 seconds.' }
  ]
  // step 2: search plan, in its four states
  .concat(on(['plan-none', 'plan-open', 'plan-search', 'plan-ask', 'plan-approved'], [
    { token: 'plan-request', text: 'Open this to reread the request as received, with any answers the requester has sent.' }
  ]))
  .concat(on(['plan-open', 'plan-search', 'plan-ask', 'plan-approved'], [
    { token: 'plan-yes', text: 'Choose this if the request is clear enough to search. It shows the Vault search to hand to Technology.' },
    { token: 'plan-no', text: 'Choose this if something needs settling first. It drafts an email to the requester with the questions.' }
  ]))
  .concat(on(['plan-search', 'plan-approved'], [
    { token: 'plan-handoff', text: 'Copies the whole search (terms, dates, accounts) as one note to paste in a message to Technology.' },
    { token: 'plan-mbox', text: 'Vault’s export screen offers PST and mbox. Only mbox works here. Catching this before the export saves Technology from running it twice.' }
  ]))
  .concat(on(['plan-search'], [
    { token: 'plan-approve', text: 'Approves the plan and moves on to Records. You can still come back from the step bar.' }
  ]))
  .concat(on(['plan-approved'], [
    { token: 'plan-continue', text: 'The plan is approved. Go on to Records, where the Vault export gets added.' }
  ]))
  .concat(on(['plan-ask'], [
    { token: 'plan-gmail', text: 'Opens a Gmail draft with the message as it stands, your edits included.' },
    { token: 'plan-reply', text: 'Paste the requester’s reply here. The plan is drafted again with their answers.' }
  ]))
  .concat(on(['plan-none', 'plan-open', 'plan-search', 'plan-ask', 'plan-approved'], [
    { token: 'plan-model', text: 'Draft the plan again with this model, for a second opinion or after the request changes.' }
  ]))
  // step 3: records, in its three states
  .concat(on(['records-empty', 'records-check', 'records-done'], [
    { token: 'records-count', text: 'Type the number Vault’s Count returned. Saving it builds the cost estimate card below.' },
    { token: 'estimate-card', text: 'What finishing this request should cost: AI processing from measured per-email costs, plus the $1-per-page policy charge. The email on the right asks the requester to confirm.' },
    { token: 'estimate-gmail', text: 'Opens a Gmail compose window with the estimate email filled in. Then click “Mark as sent” so the quoted numbers are kept.' },
    { token: 'estimate-sent', text: 'Records that the estimate went out, with the exact numbers quoted. Later changes to the estimate never alter what the requester was told.' },
    { token: 'estimate-proceed', text: 'The requester confirmed. The request moves on to the Vault export.' },
    { token: 'records-upload', text: 'Add the export Technology sends back: the .mbox from Vault, single .eml emails, or .txt files.' },
    { token: 'records-samples', text: 'Loads six fictional emails so you can try the redaction check without a real export.' }
  ]))
  .concat(on(['records-check', 'records-done'], [
    { token: 'records-models', text: 'Pick which AI checks the records. Both at once shows them side by side on the review screen.' },
    { token: 'records-check-one', text: 'Runs the redaction check on just this record, or runs it again after changing the models.' }
  ]))
  .concat(on(['records-check'], [
    { token: 'records-check-all', text: 'Checks every record that has not been checked yet, then opens the review on its own.' }
  ]))
  .concat(on(['records-done'], [
    { token: 'records-review', text: 'Every record is checked. Start deciding on the suggestions, one email at a time.' }
  ]))
  // step 4: review
  .concat([
    { screen: 'review', token: 'review-tabs', text: 'One tab per email. The chip shows how many suggestions are still undecided in each.' },
    { screen: 'review', token: 'review-mode', text: 'Turn this on, then highlight words in either pane to redact them yourself. Esc turns it off.' },
    { screen: 'review', token: 'review-category', text: 'The exemption a redaction you add is filed under. It shows in the internal log.' },
    { screen: 'review', token: 'review-find', text: 'Type a name or phrase and press Enter to redact every place it appears in this email.' },
    { screen: 'review', token: 'review-original', text: 'The email as received, with every suggestion highlighted. Click one to jump to it in the list.' },
    { screen: 'review', token: 'review-preview', text: 'What the requester would get. Gray bars are undecided, black bars are confirmed redactions.' },
    { screen: 'review', token: 'review-all', text: 'Read it through? This redacts every suggestion still undecided, in one step.' },
    { screen: 'review', token: 'review-list', text: 'Each suggestion, who found it, and why. Choose Redact or Keep; keys A and R do the same.' },
    { screen: 'review', token: 'review-next', text: 'Moves to the next email, or to sign off after the last one. Your decisions save as you go.' }
  ])
  // step 5: sign off
  .concat(on(['signoff-open', 'signoff-done'], [
    { token: 'signoff-cost', text: 'The real cost of this request: every model call’s token counts priced at list rates, plus the page charge, next to what the requester was quoted.' },
    { token: 'signoff-summary', text: 'The totals for this request. Anything in red has to be settled before you can sign off.' }
  ]))
  .concat(on(['signoff-open'], [
    { token: 'signoff-name', text: 'Your name, recorded in the internal log as the person who approved the release.' },
    { token: 'signoff-go', text: 'Saves one redacted file per email for release and an internal log of every decision.' }
  ]))
  .concat(on(['signoff-done'], [
    { token: 'signoff-zip', text: 'Everything that may go to the requester in one download: a single PDF of all the redacted records, plus each record as a text file.' },
    { token: 'signoff-files', text: 'The files that were saved. Release the plain ones; the INTERNAL log never leaves the district.' },
    { token: 'signoff-reopen', text: 'Takes the request back to the review so decisions can change. The saved files stay until you sign off again.' }
  ]));

  ProductTour.init({
    storageKey: 'records-assist-tips-on',
    tips: tips,
    onChange: function (isOn) {
      if (typeof toast === 'function') {
        toast(isOn ? 'Tips are on. Hover anything with a number to see what it does.' : 'Tips turned off.');
      }
    }
  });
  ProductTour.refresh(document.body.dataset.screen || undefined);
})();
