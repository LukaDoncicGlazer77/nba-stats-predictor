library(cbbdata)
library(data.table)

years <- 2008:2026
all_data <- list()

for (yr in years) {
  cat(sprintf("Fetching %d...\n", yr))
  tryCatch({
    df <- cbd_torvik_player_season(year=yr, min_min=0)
    df <- df[, .(player, team, year, rim_m, rim_a, rim_pct, mid_m, mid_a, mid_pct,
                 three_m, three_a, three_pct, ftm, fta, ft_pct)]
    all_data[[as.character(yr)]] <- df
  }, error = function(e) {
    cat(sprintf("  Failed for %d: %s\n", yr, e$message))
  })
  Sys.sleep(0.5)
}

combined <- rbindlist(all_data, ignore.attr=TRUE)
cat(sprintf("\nTotal rows: %d\n", nrow(combined)))
cat(sprintf("Years covered: %s\n", paste(sort(unique(combined$year)), collapse=", ")))

fwrite(combined, "shot_zones.csv")
cat("Written to shot_zones.csv\n")
