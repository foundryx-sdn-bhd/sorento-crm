# UAC customer-bulk-ops

## U1 Pager survives Edit > Save
- U1.1 From the customers list (any page, sort, search, status or group filter) open a customer: pager shows "N / M".
  Edit > Save: back on the detail page with the same query string; pager shows the same "N / M"; Next opens the next row of that list.
- U1.2 "Back to customer" from the edit page keeps the same query string.
- U1.3 Same for suppliers edit (Save and Back).

## U2 Bulk Set sales agent
- U2.1 Select 1+ customers: bulk strip shows "Set sales agent (n)".
- U2.2 Click opens a dialog with a searchable, active-agent select (label `CODE - person`); Apply disabled until an agent is chosen.
- U2.3 Apply sends all selected customer ids in one request to `POST /sales-agents/{agentId}/customers`.
- U2.4 Success: toast names the count, selection clears, list refetches and the Sales agent column shows the new code.
- U2.5 Failure (e.g. 404/422): error toast with the server message, nothing changed, selection kept.
- U2.6 User without `master_data.sales_agents.edit` sees no such action.

## U3 Bulk Unlink on contact Customers card
- U3.1 Each linked-customer row has a checkbox; header shows "Unlink (n)" when n >= 1.
- U3.2 Click parks one `contact_customer_link.unlink` pending action per selected link, shows ONE countdown with ONE Cancel; no confirm dialog.
- U3.3 Cancel withdraws all of them; rows stay linked.
- U3.4 Lapse: rows disappear after refetch; unselected rows stay.
- U3.5 Single-row Unlink keeps working as today.

## U4 Contacts list bulk Link customers (owner approved 3 Oct, flow A)
- U4.1 Select 1+ contacts: bulk strip shows "Link customers (n)" (only with `user_management.contacts.edit`).
- U4.2 Click opens a dialog with the same customer multi-picker as the contact card
  (`SearchableMultiSelect` + `useCustomerMultiPicker`); Apply disabled until 1+ customer picked.
- U4.3 Apply sends one `POST /contacts/{id}/customers` `{customer_ids}` per selected contact.
- U4.4 All ok: toast "Linked n customers to m contacts"; dialog closes; selection clears; list page, search, sort unchanged; list refetches.
- U4.5 Some fail: toast names the failed contacts with the server message; succeeded ones stay linked; failed ones stay selected.
- U4.6 No name matching: only customers the user picked are linked.

## U5 Contacts list Customers column + "No customers linked" filter (owner approved 3 Oct, Q1)
- U5.1 Contacts list response carries each contact's linked customer codes; a "Customers" column shows them
  (comma list, `truncate` + `title`, explicit `size`), empty cell when none.
- U5.2 Filter "No customers linked" (toolbar control) sends `customers=none`; BE returns only contacts with no
  `respond_contact_customers` row; count/pagination match.
- U5.3 Filter value lives in the URL like `chatbot_memory_level`; a filter change resets to page 1; after a bulk
  Link the linked contacts drop out of the filtered list on refetch.

## U6 Customers list bulk Set customer group / Remove from group (owner 3 Oct; replaces CUST-GROUP-SEED-REVIEW PR 2 name seeding)
- U6.1 Select 1+ customers: bulk strip shows "Set customer group (n)" and "Remove from group (m)" (only with `order_management.customers.edit`); n = selected customers, m = selected customers that have a group, Remove disabled at m = 0.
- U6.2 Set opens a dialog with a searchable group select (server search `searchCustomerGroupsSelect`, caller's company)
  offering "Create group <typed name>" (SearchableSelect `createOption`); Apply disabled until a group is chosen or typed.
- U6.3 Apply with an existing group: one `addCustomerGroupCustomers(groupId, customerIds)` (`POST /customer-groups/{id}/customers`, all or nothing).
  With a new name: `createCustomerGroup({name})` first, then the same assign with the new id.
- U6.4 Success: toast "n customers set to group <name>"; selection clears; list refetches; page/search/sort unchanged.
- U6.5 Failure (e.g. 422 customer of another company, 409 name taken): error toast with server message; nothing assigned; selection kept.
- U6.6 Remove from group: one deferred `customer.remove_from_group` action per selected customer that has a group
  (payload `customer_group_id`), ONE countdown + Cancel, no confirm; customers with no group are skipped.
- U6.7 No name matching anywhere: only the group the user picked or typed.
