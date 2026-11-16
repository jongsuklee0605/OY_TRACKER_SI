def run_stock(page, prev):
    log(f"[재고 모드] 상품 {len(GOODS)}개")
    stock, fails = {}, {}
    consec_fail = 0
    stopped = None

    for i, g in enumerate(GOODS, 1):
        if time.time() - T0 > TIME_LIMIT:
            stopped = f"시간 제한 ({i - 1}/{len(GOODS)}개 확인 후 중단)"
            break
        if consec_fail >= MAX_CONSEC_FAIL:
            stopped = f"연속 {consec_fail}회 실패 → 접속 차단 의심 ({i - 1}/{len(GOODS)}개 확인 후 중단)"
            break
        try:
            stock[g] = get_stock(page, g)
            consec_fail = 0
            s = stock[g]
            if len(stock) <= 3:  # 처음 몇 개는 상품명/브랜드가 맞게 읽히는지 로그로 확인
                log(f"예시 {g}: 상품명 '{s['name']}' / 브랜드 '{s['brand']}'")
            if s["options"]:
                desc = ", ".join(f"{n}({'품절' if v else '판매중'})" for n, v in s["options"].items())
                log(f"[{i}/{len(GOODS)}] {s['name']} ({g}) 옵션 {len(s['options'])}개: {desc}")
            else:
                log(f"[{i}/{len(GOODS)}] {s['name']} ({g}) {'품절' if s['soldout'] else '판매중'} (옵션 없음)")
        except NoStockInfo as e:
            consec_fail = 0
            fails[g] = str(e)[:100]
            log(f"[{i}/{len(GOODS)}] {g} 판단 불가: {str(e)[:100]}")
        except Exception as e:
            consec_fail += 1
            fails[g] = str(e)[:100]
            log(f"[{i}/{len(GOODS)}] {g} 실패: {str(e)[:100]}")
        time.sleep(random.uniform(0.5, 1.2))

    old_items = prev["items"]
    prev_errors = prev["errors"]
    now_items, now_errors, blocks = {}, {}, []
    by_brand = {}   # {브랜드: [상품별 알림글자]}  → 브랜드마다 별도 메시지로 전송

    for g in GOODS:
        old = old_items.get(g, {})
        if g in stock:
            s = stock[g]
            cur = {"name": s["name"] or old.get("name", ""), "brand": s.get("brand") or old.get("brand", ""),
                   "soldout": s["soldout"], "options": s["options"]}
        else:
            cur = {"name": old.get("name", ""), "brand": old.get("brand", ""),
                   "soldout": old.get("soldout"), "options": old.get("options", {})}
            if g in fails:
                now_errors[g] = fails[g]
            elif g in prev_errors:
                now_errors[g] = prev_errors[g]
        now_items[g] = cur

        changed = (old.get("soldout"), old.get("options", {})) != (cur["soldout"], cur["options"])
        if not (changed or ALWAYS) or g not in stock:
            continue
        tag = "⚠️ 변화 " if changed and g in old_items else ""
        title = f"{cur['name']} ({g})" if cur["name"] else g
        lines = [f"{tag}{title}"]
        opts = cur["options"] or {}
        if opts:
            sold = sum(1 for v in opts.values() if v)
            lines.append(f"  · 재고: 옵션 {len(opts)}개 중 {sold}개 품절 ({stock_label(cur['soldout'])})")
            old_opts = old.get("options") or {}
            for oname, v in opts.items():
                mark = ""
                if oname in old_opts and old_opts[oname] != v:
                    mark = f" ← 변화 (이전: {'품절' if old_opts[oname] else '판매중'})"
                lines.append(f"      - {oname}: {stock_label(v)}{mark}")
        else:
            prev_label = f" (이전: {stock_label(old.get('soldout'))})" if changed and g in old_items else ""
            lines.append(f"  · 재고: {stock_label(cur['soldout'])}{prev_label}")
        lines.append(f"  {detail_url(g)}")
        by_brand.setdefault(cur.get("brand") or NO_BRAND, []).append("\n".join(lines))

    err_lines = []
    for g, msg in fails.items():
        if prev_errors.get(g) != msg:
            label = now_items[g].get("name") or g
            err_lines.append(f"{label} ({g}) 재고 실패: {msg}")
    if stopped:
        err_lines.append(f"중단: {stopped}")
        log(f"중단: {stopped}")
    if err_lines:
        shown = err_lines[:10]
        extra = f"\n...외 {len(err_lines) - 10}건" if len(err_lines) > 10 else ""
        blocks.append("❗ 확인 실패/중단 (이전 값 유지)\n" + "\n".join(shown) + extra)

    ts = now_ts()
    sent = bool(by_brand or blocks)
    if sent:
        send_by_brand(f"📦 [올리브영 재고 {ts}]", by_brand)   # 브랜드별 별도 메시지
        if blocks:
            send_slack(f"📦 [올리브영 재고 {ts}] 상품 {len(GOODS)}개 확인", blocks)   # 실패/중단 안내
    elif NOTIFY_NO_CHANGE:
        post_slack(f"✅ [올리브영 재고 {ts}] 상품 {len(GOODS)}개 체크했으나 변경점이 없습니다. (재고 확인 {len(stock)}개)")
    else:
        log("변화 없음 (알림 생략)")

    # 여러 샤드 결과를 합쳐 '변경점 없음'을 한 번만 보내기 위한 기록
    (BASE_DIR / "olive_stock_result.json").write_text(
        json.dumps({"sent": sent, "goods": len(GOODS), "checked": len(stock)})
    )

    return {"items": now_items, "errors": now_errors}
