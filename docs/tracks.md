# Tracks

A track is the stream of releases a customer follows. Every customer starts on All customers; a
track for testers can follow an open change before it is released, so your team sees it first.

```python
moved = customer.set_track("Beta")
print(moved.track.name, moved.previous_track_id)
customer.set_track(None)  # back to All customers
```

Name the track by its name, which stays the same in every environment. Answers say which track,
release and change they come from (`check.track`, `check.release`, `check.change`).

`set_track` needs the `tracks:assign` scope. The server key preset lacks it, so a key minted from
that preset answers `403 scope_required`: add the scope to the key in the dashboard.

`server.customers.list(track=moved.track.id)` lists the customers on a track, named by its id.
